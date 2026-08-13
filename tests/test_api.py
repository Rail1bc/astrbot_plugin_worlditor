"""Web API handler 单元测试（v3）。

handler 通过 `astrbot.api.web.request` 代理读取请求（不接收 request 参数），
测试用 `bind_request_context` 把构造的 PluginRequest 绑定到当前异步上下文后
调用。FakePlugin 只挂 StateAPI / PlayAPI / EditAPI mixin 与 engine，不触碰
Star 装配。
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

pytest.importorskip("astrbot")

from astrbot.api.web import (  # noqa: E402
    PluginRequest,
    bind_request_context,
)
from astrbot_plugin_worlditor.api.edit import EditAPI  # noqa: E402
from astrbot_plugin_worlditor.api.play import PlayAPI  # noqa: E402
from astrbot_plugin_worlditor.api.routes import _ROUTES  # noqa: E402
from astrbot_plugin_worlditor.api.state import StateAPI  # noqa: E402
from astrbot_plugin_worlditor.world.engine import (  # noqa: E402
    AGENT_PLAYER_ID,
    WorldEngine,
)
from astrbot_plugin_worlditor.world.store import DEFAULT_MAP_ID, WorldStore  # noqa: E402


class FakePlugin(StateAPI, PlayAPI, EditAPI):
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


async def call_handler(
    plugin: FakePlugin, handler_name: str, body: dict | None = None, query: str = ""
):
    """把请求绑定到当前异步上下文后调用指定 handler。"""
    req = make_plugin_request(body, query)
    handler = getattr(plugin, handler_name)
    with bind_request_context(req):
        return await handler()


def _run(coro):
    return asyncio.run(coro)


def fixed_clock(hour: int):
    return lambda: datetime(2026, 8, 13, hour, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


async def _scenario(tmp_path: Path, fn):
    engine = WorldEngine(WorldStore(tmp_path / "world.db"), clock=fixed_clock(12))
    await engine.initialize()
    try:
        plugin = FakePlugin(engine)
        return await fn(plugin)
    finally:
        await engine.terminate()


# ---------- 玩家 ----------


def test_register_handler(tmp_path):
    """POST /world/player/register：返回随机 player_id 与出生地块。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(plugin, "world_register", body={})
        data = json.loads(resp.body)
        assert data["player_id"]
        assert len(data["player_id"]) == 8
        assert data["map_id"] == DEFAULT_MAP_ID
        assert data["row"] == 0 and data["col"] == 0
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


# ---------- 状态 ----------


def test_state_handler(tmp_path):
    """GET /world/state：全量地图 + 地块 + 玩家场景 + agent 位置。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(plugin, "world_state", query=f"player_id={player_id}")
        data = json.loads(resp.body)
        assert len(data["maps"]) == 1
        assert data["maps"][0]["id"] == DEFAULT_MAP_ID
        assert len(data["locations"]) == 8
        assert data["locations"][0]["connections"]["up"]["enabled"] is True
        assert data["templates"] == []
        player = data["player"]
        assert player["player_id"] == player_id
        assert player["map_id"] == DEFAULT_MAP_ID
        assert len(player["scene"]["paths"]) == 5
        agent = data["agent"]
        assert agent["player_id"] == AGENT_PLAYER_ID
        assert (agent["row"], agent["col"]) == (0, 0)
        assert data["spawn"] == {"map_id": DEFAULT_MAP_ID, "row": 0, "col": 0}

    _run(_scenario(tmp_path, fn))


def test_state_handler_without_player(tmp_path):
    """无 player_id / 玩家不存在 → player 为 null。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(plugin, "world_state")
        data = json.loads(resp.body)
        assert data["player"] is None
        resp2 = await call_handler(plugin, "world_state", query="player_id=ghost")
        assert json.loads(resp2.body)["player"] is None

    _run(_scenario(tmp_path, fn))


# ---------- 移动 ----------


def test_move_handler(tmp_path):
    """POST /world/move：按方向移动后返回新场景，玩家位置更新。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(
            plugin,
            "world_move",
            body={"player_id": player_id, "direction": "up"},
        )
        data = json.loads(resp.body)
        assert data["location"]["name"] == "街角咖啡店"
        assert (data["row"], data["col"]) == (-1, 0)
        assert plugin.engine.get_player(player_id).pos_key() == (
            DEFAULT_MAP_ID,
            -1,
            0,
        )

    _run(_scenario(tmp_path, fn))


def test_move_handler_with_path(tmp_path):
    """多路径移动：指定 path 索引。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(
            plugin,
            "world_move",
            body={"player_id": player_id, "direction": "down", "path": 1},
        )
        data = json.loads(resp.body)
        assert data["location"]["name"] == "中央公园"

    _run(_scenario(tmp_path, fn))


def test_move_handler_invalid(tmp_path):
    """非法方向 / 缺参 / 多路径缺 path / 未知玩家 → 400 error 信封。"""

    async def fn(plugin: FakePlugin):
        reg = json.loads((await call_handler(plugin, "world_register", body={})).body)
        player_id = reg["player_id"]
        resp = await call_handler(
            plugin, "world_move", body={"player_id": player_id, "direction": "north"}
        )
        data = json.loads(resp.body)
        assert resp.status_code == 400
        assert data["status"] == "error"
        assert "方向必须" in data["message"]
        resp2 = await call_handler(plugin, "world_move", body={"player_id": player_id})
        assert json.loads(resp2.body)["status"] == "error"
        resp3 = await call_handler(
            plugin,
            "world_move",
            body={"player_id": player_id, "direction": "down"},
        )
        assert json.loads(resp3.body)["status"] == "error"  # 多路径缺 path
        resp4 = await call_handler(
            plugin,
            "world_move",
            body={"player_id": "ghost", "direction": "up"},
        )
        assert resp4.status_code == 400
        assert "玩家不存在" in json.loads(resp4.body)["message"]
        # path 非整数 → 400
        resp5 = await call_handler(
            plugin,
            "world_move",
            body={"player_id": player_id, "direction": "down", "path": "0"},
        )
        assert resp5.status_code == 400

    _run(_scenario(tmp_path, fn))


# ---------- 地块 ----------


def test_location_create_handler(tmp_path):
    """POST /world/location/create：新建地块并反映到 world/state。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(
            plugin,
            "world_location_create",
            body={"row": 5, "col": 5, "name": "沙滩", "description": "海边。"},
        )
        data = json.loads(resp.body)
        assert resp.status_code == 200
        assert data["location"]["name"] == "沙滩"
        assert data["location"]["description"]["periods"][0]["items"][0]["text"] == "海边。"
        state = json.loads((await call_handler(plugin, "world_state")).body)
        assert len(state["locations"]) == 9

    _run(_scenario(tmp_path, fn))


def test_location_create_handler_invalid(tmp_path):
    """新建地块：重复坐标 / 缺 name / 非法坐标 / 非对象 body → 400。"""

    async def fn(plugin: FakePlugin):
        base = {"row": 5, "col": 5, "name": "沙滩"}
        resp = await call_handler(plugin, "world_location_create", body=base)
        assert resp.status_code == 200
        resp2 = await call_handler(plugin, "world_location_create", body=base)
        assert resp2.status_code == 400
        assert "已存在" in json.loads(resp2.body)["message"]
        resp3 = await call_handler(
            plugin, "world_location_create", body={"row": 6, "col": 6}
        )
        assert resp3.status_code == 400
        resp4 = await call_handler(
            plugin, "world_location_create", body={"row": "x", "col": 6, "name": "X"}
        )
        assert resp4.status_code == 400
        resp5 = await call_handler(
            plugin, "world_location_create", body={"row": True, "col": 6, "name": "X"}
        )
        assert resp5.status_code == 400

    _run(_scenario(tmp_path, fn))


def test_location_update_handler(tmp_path):
    """更新地块：改名、改描述、描述清空；不存在 → 400。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(
            plugin,
            "world_location_update",
            body={"row": 0, "col": 0, "name": "新广场"},
        )
        assert json.loads(resp.body)["location"]["name"] == "新广场"
        resp2 = await call_handler(
            plugin,
            "world_location_update",
            body={"row": 0, "col": 0, "description": "全新地砖。"},
        )
        desc = json.loads(resp2.body)["location"]["description"]
        assert desc["periods"][0]["items"][0]["text"] == "全新地砖。"
        resp3 = await call_handler(
            plugin,
            "world_location_update",
            body={"row": 0, "col": 0, "description": None},
        )
        assert json.loads(resp3.body)["location"]["description"] is None
        resp4 = await call_handler(
            plugin,
            "world_location_update",
            body={"row": 9, "col": 9, "name": "X"},
        )
        assert resp4.status_code == 400

    _run(_scenario(tmp_path, fn))


def test_location_delete_handler(tmp_path):
    """删除地块：成功、拒绝删除 agent 所在地块、world/state 反映。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(
            plugin, "world_location_delete", body={"row": 0, "col": 1}
        )
        assert json.loads(resp.body)["ok"] is True
        assert plugin.engine.get_location(DEFAULT_MAP_ID, 0, 1) is None
        resp2 = await call_handler(
            plugin, "world_location_delete", body={"row": 0, "col": 0}
        )
        assert resp2.status_code == 400
        assert "有玩家" in json.loads(resp2.body)["message"]
        state = json.loads((await call_handler(plugin, "world_state")).body)
        assert len(state["locations"]) == 7

    _run(_scenario(tmp_path, fn))


def test_location_move_handler(tmp_path):
    """移动地块：坐标迁移 + 全图引用重写反映到 state。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(
            plugin,
            "world_location_move",
            body={"row": -1, "col": 0, "to_row": 5, "to_col": 5},
        )
        assert json.loads(resp.body)["location"]["row"] == 5
        state = json.loads((await call_handler(plugin, "world_state")).body)
        plaza = next(
            loc for loc in state["locations"] if loc["row"] == 0 and loc["col"] == 0
        )
        up = plaza["connections"]["up"]["paths"][0]["targets"][0]
        assert (up["row"], up["col"]) == (5, 5)
        resp2 = await call_handler(
            plugin,
            "world_location_move",
            body={"row": 0, "col": 0, "to_row": 1, "to_col": 0},
        )
        assert resp2.status_code == 400
        assert "已被占用" in json.loads(resp2.body)["message"]

    _run(_scenario(tmp_path, fn))


def test_connection_update_handler(tmp_path):
    """连接槽位更新：启用 + 整槽路径替换；非法 paths → 400。"""

    async def fn(plugin: FakePlugin):
        await call_handler(
            plugin,
            "world_location_create",
            body={"row": 7, "col": 7, "name": "瞭望塔"},
        )
        resp = await call_handler(
            plugin,
            "world_connection_update",
            body={
                "row": 7,
                "col": 7,
                "direction": "down",
                "enabled": True,
                "paths": [
                    {
                        "label": "下塔回镇",
                        "reveal_target": False,
                        "targets": [{"row": 0, "col": 0}],
                    }
                ],
            },
        )
        data = json.loads(resp.body)
        conn = data["location"]["connections"]["down"]
        assert conn["enabled"] is True
        assert conn["paths"][0]["targets"][0]["row"] == 0
        # 非法 paths（非数组）
        resp2 = await call_handler(
            plugin,
            "world_connection_update",
            body={"row": 7, "col": 7, "direction": "down", "paths": "nope"},
        )
        assert resp2.status_code == 400
        # 非法方向
        resp3 = await call_handler(
            plugin,
            "world_connection_update",
            body={"row": 7, "col": 7, "direction": "diagonal"},
        )
        assert resp3.status_code == 400

    _run(_scenario(tmp_path, fn))


# ---------- 模板 ----------


def test_template_handlers(tmp_path):
    """模板：创建 / 应用（目标平移）/ 更新 / 删除。"""

    async def fn(plugin: FakePlugin):
        resp = await call_handler(
            plugin,
            "world_template_create",
            body={"id": "cafe_tpl", "name": "咖啡店模板", "row": -1, "col": 0},
        )
        assert json.loads(resp.body)["template"]["id"] == "cafe_tpl"
        resp2 = await call_handler(
            plugin,
            "world_template_apply",
            body={"id": "cafe_tpl", "row": 10, "col": 10},
        )
        loc = json.loads(resp2.body)["location"]
        assert loc["name"] == "街角咖啡店"
        assert loc["connections"]["down"]["paths"][0]["targets"][0] == {
            "row": 11,
            "col": 10,
            "weight": 1.0,
        }
        # 应用占用格 → 400
        resp3 = await call_handler(
            plugin,
            "world_template_apply",
            body={"id": "cafe_tpl", "row": 0, "col": 0},
        )
        assert resp3.status_code == 400
        # 改名
        resp4 = await call_handler(
            plugin, "world_template_update", body={"id": "cafe_tpl", "name": "新模板"}
        )
        assert json.loads(resp4.body)["template"]["name"] == "新模板"
        state = json.loads((await call_handler(plugin, "world_state")).body)
        assert state["templates"][0]["name"] == "新模板"
        # 删除
        resp5 = await call_handler(
            plugin, "world_template_delete", body={"id": "cafe_tpl"}
        )
        assert json.loads(resp5.body)["ok"] is True
        assert json.loads((await call_handler(plugin, "world_state")).body)["templates"] == []
        # 模板不存在 → 400
        resp6 = await call_handler(
            plugin, "world_template_apply", body={"id": "ghost", "row": 3, "col": 3}
        )
        assert resp6.status_code == 400

    _run(_scenario(tmp_path, fn))


def test_routes_table_complete():
    """路由表与 handler 存在性：每个端点都能在 mixin 类上解析到方法。"""
    paths = {path for path, _, _, _ in _ROUTES}
    assert paths == {
        "/world/state",
        "/world/player/register",
        "/world/move",
        "/world/player/deregister",
        "/world/location/create",
        "/world/location/update",
        "/world/location/delete",
        "/world/location/move",
        "/world/connection/update",
        "/world/template/create",
        "/world/template/update",
        "/world/template/delete",
        "/world/template/apply",
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
        (f"/astrbot_plugin_worlditor{path}", methods) for path, _, methods, _ in _ROUTES
    ]
    assert registered == expected
