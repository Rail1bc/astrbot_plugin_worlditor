"""v4.1 REST 端点测试（auth / snapshot / sse / admin / static）。

handler 是 mixin 方法：组合 FakePlugin 实例 + bind_request_context 绑定请求。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from starlette.responses import JSONResponse, StreamingResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot.api.web import PluginRequest, bind_request_context  # noqa: E402
from astrbot_plugin_worlditor.api import (  # noqa: E402
    V4AdminAPI,
    V4AuthAPI,
    V4SnapshotAPI,
    V4SseAPI,
    V4StaticAPI,
)
from astrbot_plugin_worlditor.world.identity import IdentityService  # noqa: E402
from astrbot_plugin_worlditor.world.play import PlayLoader  # noqa: E402
from astrbot_plugin_worlditor.world.v4engine import V4WorldEngine  # noqa: E402
from astrbot_plugin_worlditor.world.v4store import V4WorldStore  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class FakePlugin(V4AuthAPI, V4SnapshotAPI, V4SseAPI, V4AdminAPI, V4StaticAPI):
    def __init__(self, engine, identity, loader):
        self.v4_engine = engine
        self.identity = identity
        self.play_loader = loader


class FakeASGIRequest:
    """最小的 request 对象（PluginRequest 包装用）。"""

    def __init__(self, *, headers=None, query=None, body=b"", method="GET"):
        self.method = method
        self.url = type("U", (), {"path": "/world/v4/test"})()
        from starlette.datastructures import Headers, QueryParams

        self.headers = Headers(headers or {})
        self.cookies = {}
        self.query_params = QueryParams(query or {})
        self.client = type("C", (), {"host": "test"})()
        self._body = body

    async def body(self):
        return self._body

    async def json(self, default=None):
        try:
            return json.loads(self._body)
        except (ValueError, TypeError):
            return default


async def _call(handler, *, headers=None, query=None, body=None, method="GET"):
    """bind 请求上下文并调用 handler（在上下文内 await，保证 request 可见）。"""
    fake = FakeASGIRequest(
        headers=headers, query=query, body=body or b"", method=method
    )
    with bind_request_context(PluginRequest(fake)):
        result = handler()
        if asyncio.iscoroutine(result):
            result = await result
        return result


async def _scenario(tmp_path, fn, **identity_kwargs):
    engine = V4WorldEngine(V4WorldStore(tmp_path / "world.db"))
    await engine.initialize()
    loader = PlayLoader(
        engine, plays_dir=tmp_path / "plays", demo_dir=REPO_ROOT / "demo_play"
    )
    await loader.load_all(None)
    identity = IdentityService(engine, **identity_kwargs)
    plugin = FakePlugin(engine, identity, loader)
    try:
        return await fn(plugin, identity, engine, loader)
    finally:
        await engine.terminate()


def _json(resp):
    assert isinstance(resp, JSONResponse)
    return json.loads(resp.body)


def _token_header(token: str) -> dict:
    return {"authorization": f"Bearer {token}"}


# ---------- auth 端点 ----------


def test_register_login_flow(tmp_path):
    """注册 → 登录 → 改密 → 注销（HTTP 端点全流程）。"""

    async def fn(plugin, identity, engine, loader):
        # 注册
        resp = await _call(
            plugin.world_v4_register,
            method="POST",
            body=json.dumps({"username": "小明", "password": "pass123"}).encode(),
        )
        data = _json(resp)
        assert data["ok"] and data["token"]["tier"] == "play"
        token = data["token"]["token"]
        # 重复注册 → 400
        resp = await _call(
            plugin.world_v4_register,
            method="POST",
            body=json.dumps({"username": "小明", "password": "pass456"}).encode(),
        )
        assert _json(resp)["error"]
        # 登录
        resp = await _call(
            plugin.world_v4_login,
            method="POST",
            body=json.dumps({"username": "小明", "password": "pass123"}).encode(),
        )
        token2 = _json(resp)["token"]["token"]
        assert token2 != token
        # 改密（旧凭据失效）
        resp = await _call(
            plugin.world_v4_change_password,
            method="POST",
            headers=_token_header(token2),
            body=json.dumps(
                {"old_password": "pass123", "new_password": "newpass9"}
            ).encode(),
        )
        assert _json(resp)["ok"] is True
        # 旧 token 已吊销 → 401
        resp = await _call(plugin.world_v4_bag, headers=_token_header(token2))
        assert resp.status_code == 401
        # 重新登录后注销
        resp = await _call(
            plugin.world_v4_login,
            method="POST",
            body=json.dumps({"username": "小明", "password": "newpass9"}).encode(),
        )
        token3 = _json(resp)["token"]["token"]
        resp = await _call(
            plugin.world_v4_logout,
            method="POST",
            headers=_token_header(token3),
        )
        assert _json(resp)["ok"] is True
        assert identity.resolve(token3) is None

    _run(_scenario(tmp_path, fn))


def test_register_agent_and_admin_endpoints(tmp_path):
    """agent 注册 + admin_key 管理员；admin 端点鉴权（play 档 403）。"""

    async def fn(plugin, identity, engine, loader):
        # agent 注册
        resp = await _call(
            plugin.world_v4_register_agent,
            method="POST",
            body=json.dumps({"name": "探针"}).encode(),
        )
        agent_token = _json(resp)["token"]["token"]
        entity = engine.get_entity(_json(resp)["token"]["entity_id"])
        assert entity is not None and entity.kind == "agent"
        # 普通玩家
        resp = await _call(
            plugin.world_v4_register,
            method="POST",
            body=json.dumps({"username": "小明", "password": "pass123"}).encode(),
        )
        play_token = _json(resp)["token"]["token"]
        # play 档调 admin 端点 → 403
        resp = await _call(
            plugin.world_v4_admin_map_create,
            method="POST",
            headers=_token_header(play_token),
            body=json.dumps({"id": "m", "name": "图"}).encode(),
        )
        assert resp.status_code == 403
        # 管理员注册（admin_key）
        resp = await _call(
            plugin.world_v4_register,
            method="POST",
            body=json.dumps(
                {"username": "管理员", "password": "pass123", "admin_key": "sekret"}
            ).encode(),
        )
        admin_token = _json(resp)["token"]["token"]
        assert _json(resp)["token"]["tier"] == "admin"
        # admin 建图
        resp = await _call(
            plugin.world_v4_admin_map_create,
            method="POST",
            headers=_token_header(admin_token),
            body=json.dumps({"id": "dungeon", "name": "地下城"}).encode(),
        )
        data = _json(resp)
        assert data["ok"] and data["map"]["id"] == "dungeon"
        # admin 吊销 agent 凭据
        resp = await _call(
            plugin.world_v4_revoke,
            method="POST",
            headers=_token_header(admin_token),
            body=json.dumps({"token": agent_token}).encode(),
        )
        assert _json(resp)["ok"] is True
        assert identity.resolve(agent_token) is None
        # 邀请码（invite 模式用 admin 生成；open 模式也可生成）
        resp = await _call(
            plugin.world_v4_create_invite_codes,
            method="POST",
            headers=_token_header(admin_token),
            body=json.dumps({"count": 3}).encode(),
        )
        codes = _json(resp)["codes"]
        assert len(codes) == 3

    _run(_scenario(tmp_path, fn, admin_key="sekret"))


def test_read_token_and_snapshot(tmp_path):
    """read 档围观：read-token → state/scene 可读；bag 需 play。"""

    async def fn(plugin, identity, engine, loader):
        resp = await _call(plugin.world_v4_read_token)
        read_token = _json(resp)["token"]["token"]
        # 全量快照
        resp = await _call(plugin.world_v4_state, headers=_token_header(read_token))
        data = _json(resp)
        assert len(data["maps"]) == 1
        assert len(data["locations"]) == 41
        assert any(e["kind"] == "merchant" for e in data["entities"])
        # scene（围观任意实体）
        merchant = [e for e in engine.list_entities() if e.kind == "merchant"][0]
        resp = await _call(
            plugin.world_v4_scene,
            headers=_token_header(read_token),
            query={"entity_id": merchant.id},
        )
        data = _json(resp)
        assert data["scene"]["location"]["name"] == "小镇广场"
        # 无 token → 401
        resp = await _call(plugin.world_v4_state)
        assert resp.status_code == 401
        # bag 需 play（read 档 403）
        resp = await _call(plugin.world_v4_bag, headers=_token_header(read_token))
        assert resp.status_code == 403

    _run(_scenario(tmp_path, fn))


def test_bag_own_and_admin(tmp_path):
    """bag：play 档只能看自己；admin 可指定任意实体。"""

    async def fn(plugin, identity, engine, loader):
        info = await identity.register_human("小明", "pass123")
        await engine.give_item(info.entity_id, "apple", 2)
        # 自己
        resp = await _call(plugin.world_v4_bag, headers=_token_header(info.token))
        items = _json(resp)["items"]
        assert items[0]["item_id"] == "apple" and items[0]["count"] == 2
        # 看别人 → 403
        other = await identity.register_human("小红", "pass123")
        resp = await _call(
            plugin.world_v4_bag,
            headers=_token_header(info.token),
            query={"entity_id": other.entity_id},
        )
        assert resp.status_code == 403
        # admin 看任意
        admin = await identity.register_human("管理员", "pass123", admin_key="k")
        resp = await _call(
            plugin.world_v4_bag,
            headers=_token_header(admin.token),
            query={"entity_id": other.entity_id},
        )
        assert _json(resp)["entity_id"] == other.entity_id

    _run(_scenario(tmp_path, fn, admin_key="k"))


# ---------- admin 端点（地块/连接/实体） ----------


def test_admin_entity_and_location(tmp_path):
    """admin 实体放置/编辑/移除 + 地块创建。"""

    async def fn(plugin, identity, engine, loader):
        admin = await identity.register_human("管理员", "pass123", admin_key="k")
        h = _token_header(admin.token)
        # 实体放置
        resp = await _call(
            plugin.world_v4_admin_entity_place,
            method="POST",
            headers=h,
            body=json.dumps(
                {
                    "kind": "sign",
                    "map_id": "default",
                    "row": 1,
                    "col": 0,
                    "name": "路标",
                }
            ).encode(),
        )
        entity = _json(resp)["entity"]
        assert entity["name"] == "路标"
        # 实体编辑（attrs/state）
        resp = await _call(
            plugin.world_v4_admin_entity_update,
            method="POST",
            headers=h,
            body=json.dumps(
                {"entity_id": entity["id"], "attrs": {"gold": 9}, "state": {"ok": True}}
            ).encode(),
        )
        updated = _json(resp)["entity"]
        assert updated["attrs"] == {"gold": 9}
        # 地块创建
        resp = await _call(
            plugin.world_v4_admin_location_create,
            method="POST",
            headers=h,
            body=json.dumps(
                {"map_id": "default", "row": 20, "col": 20, "name": "新地块"}
            ).encode(),
        )
        assert _json(resp)["location"]["name"] == "新地块"
        # 连接编辑
        resp = await _call(
            plugin.world_v4_admin_connection_update,
            method="POST",
            headers=h,
            body=json.dumps(
                {
                    "map_id": "default",
                    "row": 0,
                    "col": 0,
                    "direction": "up",
                    "enabled": False,
                }
            ).encode(),
        )
        assert _json(resp)["location"]["connections"]["up"]["enabled"] is False
        # 实体移除
        resp = await _call(
            plugin.world_v4_admin_entity_remove,
            method="POST",
            headers=h,
            body=json.dumps({"entity_id": entity["id"]}).encode(),
        )
        assert _json(resp)["ok"] is True
        assert engine.get_entity(entity["id"]) is None
        # 地块删除
        resp = await _call(
            plugin.world_v4_admin_location_delete,
            method="POST",
            headers=h,
            body=json.dumps({"map_id": "default", "row": 20, "col": 20}).encode(),
        )
        assert _json(resp)["ok"] is True
        assert engine.get_location("default", 20, 20) is None

    _run(_scenario(tmp_path, fn, admin_key="k"))


# ---------- SSE ----------


def test_sse_endpoint_auth_and_type(tmp_path):
    """SSE：play 档返回 StreamingResponse；read 档/无 token 拒绝。"""

    async def fn(plugin, identity, engine, loader):
        info = await identity.register_human("小明", "pass123")
        resp = await _call(plugin.world_v4_events, headers=_token_header(info.token))
        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"
        # read 档拒绝（档位不足 403）
        read = await identity.create_read_token()
        resp = await _call(plugin.world_v4_events, headers=_token_header(read.token))
        assert resp.status_code == 403
        # 无 token
        resp = await _call(plugin.world_v4_events)
        assert resp.status_code == 401

    _run(_scenario(tmp_path, fn))


# ---------- 玩法包 web 静态资源 ----------


def test_play_web_static(tmp_path):
    """玩法包 web/ 静态资源：读取 + 路径穿越防护。"""

    async def fn(plugin, identity, engine, loader):
        # 造一个带 web/ 的社区玩法包
        play_dir = tmp_path / "plays" / "worlditor_play_ui"
        play_dir.mkdir(parents=True, exist_ok=True)
        (play_dir / "play.yaml").write_text(
            "name: worlditor_play_ui\ndisplay_name: UI\nversion: 0.1.0\n"
            'requires:\n  worlditor: ">=0.3.0"\n  plays: []\n',
            encoding="utf-8",
        )
        (play_dir / "main.py").write_text(
            "def setup(api, context):\n    pass\n", encoding="utf-8"
        )
        web_dir = play_dir / "web"
        web_dir.mkdir()
        (web_dir / "comp.js").write_text("// component", encoding="utf-8")
        await loader.load_all(None)
        info = await identity.register_human("小明", "pass123")
        h = _token_header(info.token)
        # 读取资源
        from starlette.responses import Response

        resp = await _call(
            plugin.world_v4_play_web,
            headers=h,
            query={"x": "1"},
        )
        # 路径参数需要 path_params：手动 set
        with bind_request_context(
            PluginRequest(
                FakeASGIRequest(headers=_token_header(info.token)),
                path_params={"play_id": "worlditor_play_ui", "path": "comp.js"},
            )
        ):
            resp = await plugin.world_v4_play_web()
        assert isinstance(resp, Response)
        assert resp.body == b"// component"
        # 路径穿越 → 404
        with bind_request_context(
            PluginRequest(
                FakeASGIRequest(headers=_token_header(info.token)),
                path_params={"play_id": "worlditor_play_ui", "path": "../main.py"},
            )
        ):
            resp = await plugin.world_v4_play_web()
        assert resp.status_code == 404
        # 未知玩法包 → 404
        with bind_request_context(
            PluginRequest(
                FakeASGIRequest(headers=_token_header(info.token)),
                path_params={"play_id": "nope", "path": "x"},
            )
        ):
            resp = await plugin.world_v4_play_web()
        assert resp.status_code == 404

    _run(_scenario(tmp_path, fn))
