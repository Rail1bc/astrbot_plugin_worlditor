"""v4.1 MCP 测试：工具（进程内 call_tool + stdio 端到端）+ HTTP 认证中间件。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.identity import IdentityService  # noqa: E402
from astrbot_plugin_worlditor.world.mcp import build_mcp_server  # noqa: E402
from astrbot_plugin_worlditor.world.mcp.http import (  # noqa: E402
    AuthMiddleware,
    _inject_identity,
)
from astrbot_plugin_worlditor.world.play import PlayLoader  # noqa: E402
from astrbot_plugin_worlditor.world.v4engine import V4WorldEngine  # noqa: E402
from astrbot_plugin_worlditor.world.v4store import V4WorldStore  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _loads(text: str) -> dict:
    return json.loads(text)


def _content_text(result) -> str:
    """call_tool 返回兼容：list[ContentBlock] 或 (content, structured) 元组。"""
    if isinstance(result, tuple):
        result = result[0]
    return result[0].text


async def _make_world(tmp_path: Path):
    """引擎 + demo 玩法包 + 一个 agent 凭据（先加载玩法包，出生礼包才生效）。"""
    engine = V4WorldEngine(V4WorldStore(tmp_path / "world.db"))
    await engine.initialize()
    loader = PlayLoader(
        engine,
        plays_dir=tmp_path / "plays",
        demo_dir=REPO_ROOT / "demo_play",
    )
    await loader.load_all(None)
    identity = IdentityService(engine, auth_mode="open")
    info = await identity.register_agent("测试探针")
    return engine, identity, info


async def _scenario(tmp_path: Path, fn):
    engine, identity, info = await _make_world(tmp_path)
    try:
        return await fn(engine, identity, info)
    finally:
        await engine.terminate()


# ---------- 工具（进程内 call_tool，fixed_identity 等价 stdio 绑定） ----------


def test_tools_look_move_interact(tmp_path):
    """工具全链路：look → move → who → interact（demo 商贩）→ bag → use。"""

    async def fn(engine, identity, info):
        mcp = build_mcp_server(engine, fixed_identity=info)
        # world_look
        result = await mcp.call_tool("world_look", {})
        payload = _loads(_content_text(result))
        assert "你当前位于：小镇广场" in payload["text"]
        assert payload["scene"]["map_id"] == "default"
        # world_who：广场有商贩·阿福
        result = await mcp.call_tool("world_who", {})
        payload = _loads(_content_text(result))
        assert "阿福" in payload["text"]
        # world_move up → 步行街南街口
        result = await mcp.call_tool("world_move", {"direction": "up"})
        payload = _loads(_content_text(result))
        assert "步行街·南街口" in payload["text"]
        assert (payload["scene"]["row"], payload["scene"]["col"]) == (-1, 0)
        # world_move 非法方向 → 错误文本
        result = await mcp.call_tool("world_move", {"direction": "north"})
        assert "移动失败" in _loads(_content_text(result))["text"]
        # 回广场 interact 商贩 talk
        await engine.move(info.entity_id, "down")
        merchant = [e for e in engine.list_entities() if e.kind == "merchant"][0]
        result = await mcp.call_tool(
            "world_interact", {"target_id": merchant.id, "action": "talk"}
        )
        payload = _loads(_content_text(result))
        assert "阿福" in payload["text"] and payload["ui"] is not None
        # 出生礼包（demo on_world_edited）→ 有金币，可以买苹果
        result = await mcp.call_tool(
            "world_interact", {"target_id": merchant.id, "action": "buy_apple"}
        )
        assert "苹果" in _loads(_content_text(result))["text"]
        # world_bag
        result = await mcp.call_tool("world_bag", {})
        payload = _loads(_content_text(result))
        assert payload["items"][0]["item_id"] == "apple"
        # world_use 吃苹果
        result = await mcp.call_tool("world_use", {"item_id": "apple"})
        payload = _loads(_content_text(result))
        assert "咔嚓" in payload["text"]
        assert engine.get_attrs(info.entity_id).get("energy") == 1
        # world_say cell
        result = await mcp.call_tool("world_say", {"text": "大家好"})
        assert "大家好" in _loads(_content_text(result))["text"]

    _run(_scenario(tmp_path, fn))


def test_tools_errors(tmp_path):
    """工具错误路径：无身份 / 未知物品 / 未认证。"""

    async def fn(engine, identity, info):
        # 无 fixed_identity（HTTP 模式但无 _meta）→ 报错文本
        mcp = build_mcp_server(engine)
        result = await mcp.call_tool("world_look", {})
        assert "未认证" in _loads(_content_text(result))["text"]
        # 固定身份：未知物品
        mcp2 = build_mcp_server(engine, fixed_identity=info)
        result = await mcp2.call_tool("world_use", {"item_id": "nonexistent"})
        assert "没有这种物品" in _loads(_content_text(result))["text"]
        # 喇叭不足的 world 广播
        result = await mcp2.call_tool("world_say", {"text": "hi", "scope": "world"})
        assert "喇叭" in _loads(_content_text(result))["text"]

    _run(_scenario(tmp_path, fn))


# ---------- HTTP 认证中间件 ----------


def test_inject_identity_unit():
    """_inject_identity：JSON-RPC 单请求注入 _meta；异常 body 原样返回。"""
    info = type("I", (), {"entity_id": "e123", "tier": "play"})()
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "world_move", "arguments": {"direction": "up"}},
        }
    ).encode()
    out = json.loads(_inject_identity(body, info))
    assert out["params"]["_meta"]["worlditor_entity_id"] == "e123"
    assert out["params"]["_meta"]["worlditor_tier"] == "play"
    # 已有 _meta 合并保留
    body2 = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "x", "arguments": {}, "_meta": {"progressToken": 9}},
        }
    ).encode()
    out2 = json.loads(_inject_identity(body2, info))
    assert out2["params"]["_meta"]["progressToken"] == 9
    assert out2["params"]["_meta"]["worlditor_entity_id"] == "e123"
    # 非法 body / 批处理不炸
    assert _inject_identity(b"not json", info) == b"not json"
    assert _inject_identity(b"[1,2]", info) == b"[1,2]"
    assert _inject_identity(b"", info) == b""


def test_auth_middleware_rejects(tmp_path):
    """中间件：无/坏 token → 401（不触达内部 app）。"""

    async def fn(engine, identity, info):
        called = []
        middleware = AuthMiddleware(_stub_app(called), identity)
        # 无 Authorization
        status, body = await _call_asgi(middleware, headers={})
        assert status == 401 and "未认证".encode() in body
        assert called == []
        # 坏 token
        status, body = await _call_asgi(
            middleware, headers={"authorization": "Bearer badtoken"}
        )
        assert status == 401
        assert called == []

    _run(_scenario(tmp_path, fn))


def test_auth_middleware_injects_identity(tmp_path):
    """中间件：有效 token → 放行并把身份注入 POST body 的 _meta。"""

    async def fn(engine, identity, info):
        seen = {}

        async def recording_app(scope, receive, send):
            seen["scope"] = scope
            chunks = []
            while True:
                msg = await receive()
                chunks.append(msg.get("body") or b"")
                if not msg.get("more_body", False):
                    break
            seen["body"] = b"".join(chunks)
            await _send_ok(send)

        middleware = AuthMiddleware(recording_app, identity)
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}
        ).encode()
        status, _ = await _call_asgi(
            middleware,
            headers={"authorization": f"Bearer {info.token}"},
            body=body,
            method="POST",
        )
        assert status == 200
        parsed = json.loads(seen["body"])
        assert parsed["params"]["_meta"]["worlditor_entity_id"] == info.entity_id
        assert parsed["params"]["_meta"]["worlditor_tier"] == info.tier

    _run(_scenario(tmp_path, fn))


# ---------- stdio 端到端（真实 MCP client 连接，同 AstrBot 接入方式） ----------


def test_stdio_end_to_end(tmp_path):
    """stdio 入口：MCP client 连接 → 认证 → 工具调用（真实全链路）。"""

    async def fn(engine, identity, info):
        import os

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT.parent)
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "astrbot_plugin_worlditor.world.mcp.stdio",
                "--db",
                str(tmp_path / "world.db"),
                "--token",
                info.token,
            ],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                # mcp 1.28 client：ListToolsResult.tools（元素可能是 tuple(name, Tool)）
                tool_list = tools.tools if hasattr(tools, "tools") else tools
                names = {t.name if hasattr(t, "name") else t[0] for t in tool_list}
                assert names == {
                    "world_look",
                    "world_move",
                    "world_say",
                    "world_bag",
                    "world_use",
                    "world_interact",
                    "world_who",
                }
                result = await session.call_tool("world_look", {})
                text = result.content[0].text
                payload = json.loads(text)
                assert "小镇广场" in payload["text"]
                # 无效 token 的进程：拒绝启动（无凭据时 stdio 退出）
        engine2 = V4WorldEngine(V4WorldStore(tmp_path / "world.db"))
        await engine2.initialize()
        try:
            bad = IdentityService(engine2)
            bad_info = await bad.register_agent("坏凭据")
            await bad.revoke_token(bad_info.token)  # 吊销后无效
            import subprocess

            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "astrbot_plugin_worlditor.world.mcp.stdio",
                    "--db",
                    str(tmp_path / "world.db"),
                    "--token",
                    bad_info.token,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=env,
            )
            assert proc.returncode != 0
            assert "凭据无效" in proc.stderr
        finally:
            await engine2.terminate()

    _run(_scenario(tmp_path, fn))


# ---------- ASGI 测试工具 ----------


def _stub_app(called: list):
    async def app(scope, receive, send):
        called.append(scope["path"])
        await _send_ok(send)

    return app


async def _send_ok(send):
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b"{}"})


async def _call_asgi(app, *, headers: dict, body: bytes = b"", method: str = "GET"):
    """最小 ASGI 调用：返回 (status, body)。"""
    scope = {
        "type": "http",
        "method": method,
        "path": "/world/mcp",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
        "client": ("test", 123),
        "scheme": "http",
        "server": ("test", 80),
        "http_version": "1.1",
    }
    body_sent = [False]

    async def receive():
        if not body_sent[0]:
            body_sent[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status = [0]
    resp_body = bytearray()

    async def send(message):
        if message["type"] == "http.response.start":
            status[0] = message["status"]
        elif message["type"] == "http.response.body":
            resp_body.extend(message.get("body") or b"")

    await app(scope, receive, send)
    return status[0], bytes(resp_body)
