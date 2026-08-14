"""v4 引擎扩展测试：地图编辑原语（v4.1 admin 端点用）+ 事件流订阅（SSE 出口）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.v4engine import (  # noqa: E402
    V4WorldEngine,
    WorldError,
)
from astrbot_plugin_worlditor.world.v4store import V4WorldStore  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def make_engine(db_path: Path, clock=None) -> V4WorldEngine:
    return V4WorldEngine(V4WorldStore(db_path), clock=clock)


async def _scenario(tmp_path: Path, fn, *, clock=None):
    engine = make_engine(tmp_path / "world.db", clock=clock)
    await engine.initialize()
    try:
        return await fn(engine)
    finally:
        await engine.terminate()


# ---------- 地图编辑原语 ----------


def test_create_update_delete_location(tmp_path):
    """地块 CRUD：新建/改名/改描述/删除（级联）。"""

    async def fn(engine: V4WorldEngine):
        edited = []
        engine.register_world_event(
            "on_world_edited", lambda api, what: edited.append(what)
        )
        loc = await engine.create_location(
            "default", 10, 10, "新地块", description="一片新地。"
        )
        assert loc.name == "新地块"
        assert engine.get_location("default", 10, 10) is loc
        with pytest.raises(WorldError, match="已存在"):
            await engine.create_location("default", 10, 10, "重复")
        await engine.update_location("default", 10, 10, name="改名地块")
        assert loc.name == "改名地块"
        await engine.update_location("default", 10, 10, description=None)
        assert loc.description is None
        await engine.delete_location("default", 10, 10)
        assert engine.get_location("default", 10, 10) is None
        assert any(w.get("op") == "create_location" for w in edited)
        assert any(w.get("op") == "delete_location" for w in edited)

    _run(_scenario(tmp_path, fn))


def test_move_location_rewrites_refs_and_entities(tmp_path):
    """移动地块：全图引用重写 + 实体跟随。"""

    async def fn(engine: V4WorldEngine):
        # 把告示牌移到 (-1,0)（地块移动前），再移动地块 → 实体跟随
        sign = [e for e in engine.list_entities() if e.kind == "sign"][0]
        await engine.move_entity(sign.id, "default", -1, 0)
        # 把 (-1,0) 步行街·南街口移到 (10, 0)
        await engine.move_location("default", -1, 0, 10, 0)
        assert engine.get_location("default", -1, 0) is None
        moved = engine.get_location("default", 10, 0)
        assert moved is not None and moved.name == "步行街·南街口"
        assert sign.pos_key() == ("default", 10, 0)
        # 广场 (0,0) 的 up 路径目标已重写为 (10,0)
        plaza = engine.get_location("default", 0, 0)
        main = plaza.connections["up"].paths[0].targets[0]
        assert (main.row, main.col) == (10, 0)
        with pytest.raises(WorldError, match="已被占用"):
            await engine.move_location("default", 10, 0, 0, 0)

    _run(_scenario(tmp_path, fn))


def test_update_connection(tmp_path):
    """连接槽位更新：enabled / paths 整体替换。"""

    async def fn(engine: V4WorldEngine):
        plaza = engine.get_location("default", 0, 0)
        assert plaza.connections["up"].enabled is True  # 种子默认连步行街
        await engine.update_connection("default", 0, 0, "up", enabled=False)
        assert plaza.connections["up"].enabled is False
        await engine.update_connection(
            "default",
            0,
            0,
            "up",
            enabled=True,
            paths=[
                {
                    "label": {
                        "periods": [
                            {
                                "start": "00:00",
                                "end": "24:00",
                                "items": [{"text": "新路", "weight": 1}],
                            }
                        ]
                    },
                    "reveal_target": True,
                    "targets": [{"row": 5, "col": 1}],
                }
            ],
        )
        assert plaza.connections["up"].paths[0].targets[0].row == 5
        with pytest.raises(WorldError, match="方向"):
            await engine.update_connection("default", 0, 0, "north")

    _run(_scenario(tmp_path, fn))


def test_map_crud(tmp_path):
    """地图 CRUD：新建/更新（多图前端支持）。"""

    async def fn(engine: V4WorldEngine):
        m = await engine.create_map(
            "dungeon", "地下城", description="幽暗的地牢。", spawn_row=1, spawn_col=1
        )
        assert engine.get_map("dungeon") is m
        with pytest.raises(WorldError, match="已存在"):
            await engine.create_map("dungeon", "重复")
        await engine.update_map("dungeon", name="深渊", spawn_row=2, spawn_col=3)
        assert m.name == "深渊" and (m.spawn_row, m.spawn_col) == (2, 3)
        with pytest.raises(WorldError, match="不存在"):
            await engine.update_map("nope", name="x")

    _run(_scenario(tmp_path, fn))


def test_update_entity_fields(tmp_path):
    """实体字段更新（admin 编辑：name/desc/attrs/state 整体替换）。"""

    async def fn(engine: V4WorldEngine):
        sign = [e for e in engine.list_entities() if e.kind == "sign"][0]
        changed = []
        engine.register_world_event(
            "on_entity_changed", lambda api, e, c: changed.append(e.id)
        )
        await engine.update_entity(
            sign.id,
            name="新告示牌",
            desc="崭新的牌子。",
            attrs={"price": 5},
            state={"clean": True},
        )
        assert sign.name == "新告示牌"
        assert sign.attrs == {"price": 5}
        assert sign.state == {"clean": True}
        assert sign.id in changed
        with pytest.raises(WorldError, match="实体不存在"):
            await engine.update_entity("missing", name="x")

    _run(_scenario(tmp_path, fn))


# ---------- 事件流订阅（SSE 出口） ----------


def test_subscribe_receives_events(tmp_path):
    """订阅者收到事件 payload（含实体与事件字段）；unsubscribe 停止。"""

    async def fn(engine: V4WorldEngine):
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        queue = engine.subscribe()  # 先建实体再订阅（place 触发 on_world_edited）
        await engine.say(player.id, "hello")
        payload = await asyncio.wait_for(queue.get(), timeout=2)
        assert payload["event"] == "on_say"
        assert payload["text"] == "hello" and payload["scope"] == "cell"
        assert payload["entity"]["id"] == player.id
        assert payload["ts"] > 0
        # 移动事件
        await engine.move(player.id, "up")
        move_payload = await asyncio.wait_for(queue.get(), timeout=2)
        assert move_payload["event"] == "on_entity_move"
        assert move_payload["from"] == ["default", 0, 0]
        assert move_payload["to"] == ["default", -1, 0]
        # 移动触发 on_entity_move + on_entity_enter 两个事件，先清空队列
        while not queue.empty():
            queue.get_nowait()
        # unsubscribe 后不再收到
        engine.unsubscribe(queue)
        await engine.say(player.id, "nobody hears")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.3)

    _run(_scenario(tmp_path, fn))


def test_subscribe_queue_full_drops_oldest(tmp_path):
    """队列满丢最旧（慢消费者不阻塞世界）。"""

    async def fn(engine: V4WorldEngine):
        queue = engine.subscribe()
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        for i in range(250):  # 超过 maxsize=200
            await engine.say(player.id, f"msg-{i}")
        # 队列不阻塞（世界继续工作），最终持有最新事件
        latest = None
        while not queue.empty():
            latest = queue.get_nowait()
        assert latest is not None and latest["text"] == "msg-249"

    _run(_scenario(tmp_path, fn))


def test_tick_not_pushed_to_subscribers(tmp_path):
    """on_tick 不入事件流（高频心跳不推 SSE）。"""

    class FakeClock:
        def __init__(self):
            self.ts = 1000.0

        def __call__(self):
            from datetime import datetime
            from zoneinfo import ZoneInfo

            return datetime.fromtimestamp(self.ts, tz=ZoneInfo("Asia/Shanghai"))

        def advance(self, s):
            self.ts += s

    async def fn(engine: V4WorldEngine, clock: FakeClock):
        queue = engine.subscribe()
        runs = []
        engine.register_world_event("on_tick", _tick_append(runs), interval=1)
        await engine._tick_once()
        clock.advance(1)
        await engine._tick_once()
        assert len(runs) == 2  # tick 跑了
        assert queue.empty()  # 但没推给订阅者

    clock = FakeClock()
    _run(_scenario(tmp_path, lambda e: fn(e, clock), clock=clock))


def _tick_append(runs: list):
    async def handler(api, dt: float):
        runs.append(dt)

    return handler
