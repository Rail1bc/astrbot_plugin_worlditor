"""Web API handler 单元测试。

handler 通过 `astrbot.api.web.request` 代理读取请求（不接收 request 参数），
测试用 `bind_request_context` 把构造的 PluginRequest 绑定到当前异步上下文后
调用。FakePlugin 只挂 StateAPI / PlayAPI mixin 与 engine，不触碰 Star 装配。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot.api.web import (  # noqa: E402
    PluginRequest,
    bind_request_context,
)
from astrbot_plugin_worlditor.api.play import PlayAPI  # noqa: E402
from astrbot_plugin_worlditor.api.routes import _ROUTES  # noqa: E402
from astrbot_plugin_worlditor.api.state import StateAPI  # noqa: E402
from astrbot_plugin_worlditor.world.engine import (  # noqa: E402
    AGENT_PLAYER_ID,
    WorldEngine,
)
from astrbot_plugin_worlditor.world.store import AGENT_START_LOCATION, WorldStore  # noqa: E402


class FakePlugin(StateAPI, PlayAPI):
    """只装配 mixin 与 engine，模拟 main.py 中 Star 的 handler 挂载形态。"""

    def __init__(self, engine: WorldEngine) -> None:
        self.engine = engine


def make_plugin_request(body: dict | None = None, query: str = "") -> PluginRequest:
    """构造带 JSON body 的 PluginRequest（需与 handler 在同一异步上下文）。"""

    async def receive() -> dict:
        return {
            "type": "http.request",
            "body": json.dumps(body or {}).encode("utf-8"),
            "more_body": False,
        }

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/x",
        "raw_path": b"/x",
        "query_string": query.encode("utf-8"),
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    return PluginRequest(Request(scope, receive))


async def call_handler(plugin: FakePlugin, handler_name: str, body: dict | None = None, query: str = ""):
    """把请求绑定到当前异步上下文后调用指定 handler。"""
    req = make_plugin_request(body, query)
    handler = getattr(plugin, handler_name)
    with bind_request_context(req):
        return await handler()


def _run(coro):
    return asyncio.run(coro)


async def _scenario(tmp_path: Path, fn):
    engine = WorldEngine(WorldStore(tmp_path / "world.db"))
    await engine.initialize()
    try:
        plugin = FakePlugin(engine)
        return await fn(plugin)
    finally:
        await engine.terminate()


def test_register_handler(tmp_path):
    """POST /world/player/register：返回随机 player_id 与起始地块。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(plugin, "world_register", body={})
        data = json.loads(resp.body)
        assert data["player_id"]
        assert len(data["player_id"]) == 8
        assert data["location_id"] == AGENT_START_LOCATION
        assert data["location_name"] == "小镇广场"
        assert plugin.engine.get_player(data["player_id"]) is not None

    _run(_scenario(tmp_path, fn))


def test_register_with_name(tmp_path):
    """注册携带 name。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(plugin, "world_register", body={"name": "小雾"})
        data = json.loads(resp.body)
        player = plugin.engine.get_player(data["player_id"])
        assert player is not None and player.name == "小雾"

    _run(_scenario(tmp_path, fn))


def test_register_non_string_name_rejected(tmp_path):
    """name 非字符串 → 400。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(plugin, "world_register", body={"name": 123})
        data = json.loads(resp.body)
        assert resp.status_code == 400
        assert data["status"] == "error"

    _run(_scenario(tmp_path, fn))


def test_state_handler(tmp_path):
    """GET /world/state：全量地图 + 玩家场景 + agent 位置。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(plugin, "world_state", query=f"player_id={player_id}")
        data = json.loads(resp.body)
        assert len(data["locations"]) == 8
        assert len(data["exits"]) == 18
        player = data["player"]
        assert player["player_id"] == player_id
        assert player["location_id"] == AGENT_START_LOCATION
        assert len(player["scene"]["exits"]) == 4
        agent = data["agent"]
        assert agent["player_id"] == AGENT_PLAYER_ID
        assert agent["location_id"] == AGENT_START_LOCATION

    _run(_scenario(tmp_path, fn))


def test_state_handler_without_player(tmp_path):
    """无 player_id / 玩家不存在 → player 为 null。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(plugin, "world_state")
        data = json.loads(resp.body)
        assert data["player"] is None
        assert data["agent"]["location_id"] == AGENT_START_LOCATION
        # 玩家不存在
        resp2 = await call_handler(plugin, "world_state", query="player_id=ghost")
        data2 = json.loads(resp2.body)
        assert data2["player"] is None

    _run(_scenario(tmp_path, fn))


def test_move_handler(tmp_path):
    """POST /world/move：移动后返回新场景，玩家位置更新。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(
            plugin,
            "world_move",
            body={"player_id": player_id, "exit_id": "town_plaza_cafe"},
        )
        data = json.loads(resp.body)
        assert data["location"]["id"] == "town_cafe"
        assert plugin.engine.get_player(player_id).location_id == "town_cafe"

    _run(_scenario(tmp_path, fn))


def test_move_handler_invalid(tmp_path):
    """非法出口 / 缺参 → 400 error 信封。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(
            plugin,
            "world_move",
            body={"player_id": player_id, "exit_id": "no_such_exit"},
        )
        data = json.loads(resp.body)
        assert resp.status_code == 400
        assert data["status"] == "error"
        assert "出口不存在" in data["message"]
        # 缺 exit_id
        resp2 = await call_handler(plugin, "world_move", body={"player_id": player_id})
        data2 = json.loads(resp2.body)
        assert resp2.status_code == 400
        assert data2["status"] == "error"

    _run(_scenario(tmp_path, fn))


def test_deregister_handler(tmp_path):
    """POST /world/player/deregister：注销后玩家消失，agent 不可注销。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(
            plugin, "world_deregister", body={"player_id": player_id}
        )
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert plugin.engine.get_player(player_id) is None

    _run(_scenario(tmp_path, fn))


def test_routes_table_complete():
    """路由表与 handler 存在性：每个端点都能在 mixin 类上解析到方法。"""
    paths = {path for path, _, _, _ in _ROUTES}
    assert paths == {
        "/world/state",
        "/world/player/register",
        "/world/move",
        "/world/player/deregister",
    }
    for _, handler, methods, desc in _ROUTES:
        assert methods, f"路由 {handler} 缺少 HTTP 方法"
        assert desc, f"路由 {handler} 缺少描述"
        assert hasattr(FakePlugin, handler), f"handler {handler} 未挂载在 FakePlugin 上"


def test_main_wires_routes(monkeypatch, tmp_path):
    """main.py 的 Star 装配：以插件前缀注册全部路由（构造期即校验拼写）。"""
    from astrbot_plugin_worlditor import main as main_mod

    registered: list[tuple[str, list[str]]] = []

    class FakeContext:
        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, methods))

    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: tmp_path),
    )
    main_mod.WorlditorPlugin(FakeContext())
    expected = [
        (f"/astrbot_plugin_worlditor{path}", methods)
        for path, _, methods, _ in _ROUTES
    ]
    assert registered == expected
