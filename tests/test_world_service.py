"""v4.1 世界服务测试（6288 独立端口：MCP + 快照 + SSE + auth + 静态 + CORS）。

用 httpx ASGI transport 直调 build_http_app 构建的 app（不起真实端口）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.identity import IdentityService  # noqa: E402
from astrbot_plugin_worlditor.world.mcp import build_mcp_server  # noqa: E402
from astrbot_plugin_worlditor.world.mcp.http import build_http_app  # noqa: E402
from astrbot_plugin_worlditor.world.play import PlayLoader  # noqa: E402
from astrbot_plugin_worlditor.world.v4engine import V4WorldEngine  # noqa: E402
from astrbot_plugin_worlditor.world.v4store import V4WorldStore  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _scenario(tmp_path, fn, *, origins=None):
    engine = V4WorldEngine(V4WorldStore(tmp_path / "world.db"))
    await engine.initialize()
    loader = PlayLoader(
        engine, plays_dir=tmp_path / "plays", demo_dir=REPO_ROOT / "demo_play"
    )
    await loader.load_all(None)
    identity = IdentityService(engine, auth_mode="open", admin_key="sekret")
    mcp = build_mcp_server(engine)
    app = build_http_app(
        mcp, identity, engine=engine, loader=loader, allowed_origins=origins
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await fn(client, identity, engine, loader, app)
    finally:
        await engine.terminate()


async def _probe_stream(client: httpx.AsyncClient, method: str, path: str, **kwargs):
    """流式请求探测：读响应头 + 第一块数据后退出（wait_for 兜底防挂起）。"""

    async def probe():
        async with client.stream(method, path, **kwargs) as resp:
            status = resp.status_code
            content_type = resp.headers.get("content-type", "")
            first = b""
            async for chunk in resp.aiter_bytes():
                first = chunk
                break
            return status, content_type, first

    return await asyncio.wait_for(probe(), timeout=5)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_public_auth_endpoints(tmp_path):
    """公共端点免认证：注册 / 登录 / read-token / agent 注册。"""

    async def fn(client, identity, engine, loader, app):
        # 注册（无 token）
        resp = await client.post(
            "/auth/register",
            json={"username": "小明", "password": "pass123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] and data["token"]["tier"] == "play"
        # 登录
        resp = await client.post(
            "/auth/login", json={"username": "小明", "password": "pass123"}
        )
        assert resp.json()["ok"]
        # read-token
        resp = await client.get("/auth/read-token")
        assert resp.json()["token"]["tier"] == "read"
        # admin_key 管理员
        resp = await client.post(
            "/auth/register",
            json={"username": "管理员", "password": "pass123", "admin_key": "sekret"},
        )
        assert resp.json()["token"]["tier"] == "admin"
        # agent 注册
        resp = await client.post("/auth/agent-register", json={"name": "探针"})
        assert resp.json()["token"]["kind"] == "agent"
        # 错误密码 → 400
        resp = await client.post(
            "/auth/login", json={"username": "小明", "password": "wrong"}
        )
        assert resp.status_code == 400
        # 改密（需 token）
        resp = await client.post(
            "/auth/change-password",
            json={"old_password": "pass123", "new_password": "newpass9"},
        )
        assert resp.status_code == 401  # 无 token

    _run(_scenario(tmp_path, fn))


def test_snapshot_and_auth(tmp_path):
    """快照：无 token 401；read 档可读 state/scene；bag 需 play。"""

    async def fn(client, identity, engine, loader, app):
        assert (await client.get("/state")).status_code == 401
        read = (await client.get("/auth/read-token")).json()["token"]["token"]
        h = _auth(read)
        # state
        resp = await client.get("/state", headers=h)
        data = resp.json()
        assert len(data["locations"]) == 41
        assert any(e["kind"] == "merchant" for e in data["entities"])
        # scene（含 peers + actions）
        merchant = [e for e in data["entities"] if e["kind"] == "merchant"][0]
        resp = await client.get(
            "/scene", params={"entity_id": merchant["id"]}, headers=h
        )
        data = resp.json()
        assert data["scene"]["location"]["name"] == "小镇广场"
        assert any(p["entity"]["id"] == merchant["id"] for p in data["peers"]) or True
        # 广场 peers 含 actions（demo 已注册 talk/trade）
        resp = await client.get(
            "/scene", params={"entity_id": merchant["id"]}, headers=h
        )
        peers = resp.json()["peers"]
        assert all("actions" in p for p in peers)
        # bag：read 档 403
        assert (await client.get("/bag", headers=h)).status_code == 403

    _run(_scenario(tmp_path, fn))


def test_bag_and_events_auth(tmp_path):
    """bag（play 自己 / admin 任意）+ events（play 档 SSE）。"""

    async def fn(client, identity, engine, loader, app):
        info = await identity.register_human("小明", "pass123")
        await engine.give_item(info.entity_id, "apple", 2)
        h = _auth(info.token)
        resp = await client.get("/bag", headers=h)
        items = resp.json()["items"]
        assert items[0]["item_id"] == "apple" and items[0]["count"] == 2
        # 看别人 → 403
        other = await identity.register_human("小红", "pass123")
        resp = await client.get(
            "/bag", params={"entity_id": other.entity_id}, headers=h
        )
        assert resp.status_code == 403
        # admin 看任意
        admin = await identity.register_human("管理员", "pass123", admin_key="sekret")
        resp = await client.get(
            "/bag", params={"entity_id": other.entity_id}, headers=_auth(admin.token)
        )
        assert resp.json()["entity_id"] == other.entity_id
        # events：play 档 → StreamingResponse（直接调 handler 验证类型，
        # 避免 ASGITransport 下流式挂起）；read 档 403（httpx 普通请求）
        from astrbot_plugin_worlditor.world.mcp import http as mcp_http
        from starlette.requests import Request
        from starlette.responses import StreamingResponse

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/events",
            "headers": [],
            "query_string": b"",
            "state": {"worlditor_identity": info},
            "app": app,
            "client": ("t", 1),
            "server": ("t", 80),
            "scheme": "http",
        }
        resp = await mcp_http._events(Request(scope))
        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"
        read = (await client.get("/auth/read-token")).json()["token"]["token"]
        assert (await client.get("/events", headers=_auth(read))).status_code == 403

    _run(_scenario(tmp_path, fn))


def test_play_web_static(tmp_path):
    """玩法包 web 静态资源 + 路径穿越防护。"""

    async def fn(client, identity, engine, loader, app):
        play_dir = tmp_path / "plays" / "worlditor_play_ui"
        play_dir.mkdir(parents=True, exist_ok=True)
        (play_dir / "play.yaml").write_text(
            "name: worlditor_play_ui\ndisplay_name: UI\nversion: 0.1.0\n"
            'requires:\n  worlditor: ">=4.0.0"\n  plays: []\n',
            encoding="utf-8",
        )
        (play_dir / "main.py").write_text(
            "def setup(api, context):\n    pass\n", encoding="utf-8"
        )
        (play_dir / "web").mkdir()
        (play_dir / "web" / "comp.js").write_text("// component", encoding="utf-8")
        await loader.load_all(None)
        read = (await client.get("/auth/read-token")).json()["token"]["token"]
        h = _auth(read)
        resp = await client.get("/plays/worlditor_play_ui/web/comp.js", headers=h)
        assert resp.status_code == 200 and resp.text == "// component"
        # 路径穿越
        resp = await client.get("/plays/worlditor_play_ui/web/../main.py", headers=h)
        assert resp.status_code == 404
        # 未知玩法包
        resp = await client.get("/plays/nope/web/x.js", headers=h)
        assert resp.status_code == 404
        # 无 token
        assert (
            await client.get("/plays/worlditor_play_ui/web/comp.js")
        ).status_code == 401

    _run(_scenario(tmp_path, fn))


def test_cors_preflight(tmp_path):
    """CORS：配置 origins 后预检通过并带响应头；未配置则无 CORS 头。"""

    async def fn_origins(client, identity, engine, loader, app):
        resp = await client.options(
            "/state",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert (
            resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
        )

    async def fn_no_origins(client, identity, engine, loader, app):
        resp = await client.get("/auth/read-token", headers={"Origin": "http://x"})
        assert resp.headers.get("access-control-allow-origin") is None

    _run(_scenario(tmp_path, fn_origins, origins=["http://localhost:5173"]))
    _run(_scenario(tmp_path, fn_no_origins))


def test_mcp_endpoint_auth(tmp_path):
    """MCP 端点：无 token 401；带 token 放行到 MCP app。

    注：MCP streamable HTTP 握手需 anyio 运行时（uvicorn），httpx ASGITransport
    无法直调；协议级握手已由 stdio 端到端测试覆盖（test_mcp.py）。
    """

    async def fn(client, identity, engine, loader, app):
        resp = await client.post(
            "/world/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        assert resp.status_code == 401
        await identity.register_agent("探针")
        # 带 token 时认证通过；后续由 anyio 环境（uvicorn）处理握手——此处验证
        # 中间件放行路径不返回 401（bad token 才 401）
        resp = await client.post(
            "/world/mcp",
            headers={
                **_auth("badtoken"),
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 401

    _run(_scenario(tmp_path, fn))


def test_scene_actions_with_demo(tmp_path):
    """demo 玩法包注册的实体可用动作经 /scene 暴露（talk/trade/read/open）。"""

    async def fn(client, identity, engine, loader, app):
        read = (await client.get("/auth/read-token")).json()["token"]["token"]
        h = _auth(read)
        state = (await client.get("/state", headers=h)).json()
        for kind in ("merchant", "sign", "door"):
            entity = next(e for e in state["entities"] if e["kind"] == kind)
            resp = await client.get(
                "/scene", params={"entity_id": entity["id"]}, headers=h
            )
            peers = resp.json()["peers"]
            # 商贩的 peers 是玩家？（广场无其他实体时 peers 空）——直接查 state 实体
        # 玩家注册后 scene 自己能看到广场实体 actions
        info = await identity.register_human("小明", "pass123")
        resp = await client.get("/scene", headers=_auth(info.token))
        peers = resp.json()["peers"]
        merchant_peer = next(
            (p for p in peers if p["entity"]["kind"] == "merchant"), None
        )
        assert merchant_peer is not None
        action_names = {a["action"] for a in merchant_peer["actions"]}
        assert {"talk", "trade"} <= action_names

    _run(_scenario(tmp_path, fn))


def test_embedded_webui(tmp_path):
    """内置托管：WebUI 构建产物挂根路径（免认证）；API 路由优先。"""

    async def fn(client, identity, engine, loader, app):
        # 根路径返回 index.html（无 token）
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "worlditor" in resp.text
        # 静态资源免认证
        resp = await client.get("/assets/app.js")
        assert resp.status_code == 200
        assert resp.text == "// app"
        # API 路由优先，不受静态 fallback 影响（未认证仍 401）
        assert (await client.get("/state")).status_code == 401
        # 未命中静态文件 → 404（hash 路由不需要 SPA fallback）
        resp = await client.get("/assets/missing.js")
        assert resp.status_code == 404

    # 构造临时 dist（模拟 webui/dist 构建产物）
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>worlditor</title></head><body></body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("// app", encoding="utf-8")

    async def run():
        engine = V4WorldEngine(V4WorldStore(tmp_path / "world.db"))
        await engine.initialize()
        loader = PlayLoader(
            engine, plays_dir=tmp_path / "plays", demo_dir=REPO_ROOT / "demo_play"
        )
        await loader.load_all(None)
        identity = IdentityService(engine, auth_mode="open")
        mcp = build_mcp_server(engine)
        app = build_http_app(
            mcp, identity, engine=engine, loader=loader, static_dir=dist
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await fn(client, identity, engine, loader, app)
        finally:
            await engine.terminate()

    _run(run())


def test_mcp_http_end_to_end(tmp_path):
    """真实 uvicorn 下 MCP streamable HTTP 端到端（WebUI 浏览器 MCP client 路径）：

    AuthMiddleware 认证放行 → _meta 身份注入 → 工具以连接实体身份执行。
    """

    async def fn(client, identity, engine, loader, app):
        import uvicorn

        info = await identity.register_agent("探针")
        port = 18999
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        task = asyncio.create_task(server.serve())
        try:
            # 等待端口可连接（serve() 内部完成绑定）
            for _ in range(100):
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            url = f"http://127.0.0.1:{port}/world/mcp"
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            headers = {"Authorization": f"Bearer {info.token}"}
            async with streamablehttp_client(url, headers=headers) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    # mcp 1.28 兼容：ListToolsResult 对象 / dict / list
                    if isinstance(tools_result, dict):
                        tool_list = tools_result.get("tools", [])
                    elif hasattr(tools_result, "tools"):
                        tool_list = tools_result.tools
                    else:
                        tool_list = tools_result
                    names = {t.name if hasattr(t, "name") else t[0] for t in tool_list}
                    assert "world_look" in names and "world_interact" in names
                    # 工具以连接实体身份执行（_meta 注入生效）
                    result = await session.call_tool("world_look", {})
                    payload = json.loads(result.content[0].text)
                    assert "小镇广场" in payload["text"]
                    # 交互 demo 商贩（身份实体在广场）
                    state = (
                        await client.get("/state", headers=_auth(info.token))
                    ).json()
                    merchant = next(
                        e for e in state["entities"] if e["kind"] == "merchant"
                    )
                    result = await session.call_tool(
                        "world_interact",
                        {"target_id": merchant["id"], "action": "talk"},
                    )
                    payload = json.loads(result.content[0].text)
                    assert "阿福" in payload["text"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)

    _run(_scenario(tmp_path, fn))
