"""v4 世界引擎单元测试（DESIGN_V4.md 契约）。

以 namespace package 加载插件（与 test_engine.py 相同模式）；时钟与 PRNG
注入保证确定性；每个测试 ``asyncio.run`` 起单循环（aiosqlite 连接绑定循环）。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.v4engine import (  # noqa: E402
    BROADCAST_COOLDOWN_SECONDS,
    V4WorldEngine,
    WorldError,
)
from astrbot_plugin_worlditor.world.v4model import (  # noqa: E402
    Effect,
    InteractionRequest,
    InteractionResult,
    ItemDef,
)
from astrbot_plugin_worlditor.world.v4store import (  # noqa: E402
    MEGAPHONE_ITEM_ID,
    V4WorldStore,
)

SH_TZ = ZoneInfo("Asia/Shanghai")
SEED_LOCATION_COUNT = 41
SEED_ENTITY_COUNT = 3  # 商贩·阿福 / 告示牌 / 木门


class FakeClock:
    """可变时钟：tick/冷却测试用。"""

    def __init__(self, ts: float = 1750000000.0) -> None:
        self.ts = ts

    def __call__(self) -> datetime:
        return datetime.fromtimestamp(self.ts, tz=SH_TZ)

    def advance(self, seconds: float) -> None:
        self.ts += seconds


def fixed_clock(hour: int, minute: int = 0):
    return lambda: datetime(2026, 8, 13, hour, minute, tzinfo=SH_TZ)


def _run(coro):
    return asyncio.run(coro)


def make_engine(db_path: Path, clock=None, rand=None) -> V4WorldEngine:
    return V4WorldEngine(
        V4WorldStore(db_path), clock=clock or fixed_clock(12), rand=rand
    )


async def _scenario(tmp_path: Path, fn, *, clock=None, rand=None):
    engine = make_engine(tmp_path / "world.db", clock=clock, rand=rand)
    await engine.initialize()
    try:
        return await fn(engine, clock)
    finally:
        await engine.terminate()


# ---------- 播种与基础状态 ----------


def test_seed_world(tmp_path):
    """种子世界 v4：41 地块 + 3 个种子实体 + 2 个种子物品。"""

    async def fn(engine: V4WorldEngine, clock=None):
        assert len(engine.list_locations()) == SEED_LOCATION_COUNT
        assert len(engine.list_entities()) == SEED_ENTITY_COUNT
        assert len(engine.store.items) == 2
        assert MEGAPHONE_ITEM_ID in engine.store.items
        assert "apple" in engine.store.items
        kinds = {e.kind for e in engine.list_entities()}
        assert kinds == {"merchant", "sign", "door"}
        plaza = engine.list_entities(row=0, col=0)
        assert any(e.kind == "merchant" for e in plaza)
        door = [e for e in engine.list_entities() if e.kind == "door"][0]
        assert door.state.get("open") is False

    _run(_scenario(tmp_path, fn))


def test_seed_is_idempotent(tmp_path):
    """重复初始化不重复播种（幂等）。"""

    async def fn(engine: V4WorldEngine, clock=None):
        await engine.terminate()
        engine2 = make_engine(tmp_path / "world.db")
        await engine2.initialize()
        try:
            assert len(engine2.list_entities()) == SEED_ENTITY_COUNT
            assert len(engine2.store.items) == 2
        finally:
            await engine2.terminate()

    _run(_scenario(tmp_path, fn))


# ---------- 实体放置 / 移除（B8：地图编辑内容） ----------


def test_place_entity(tmp_path):
    """放置实体：uuid id、name 缺省取 kind label、地块必须存在。"""

    async def fn(engine: V4WorldEngine, clock=None):
        engine.register_entity_kind("workshop", label="工坊")
        e = await engine.place_entity("workshop", "default", 1, 0, desc="叮叮当当。")
        assert len(e.id) == 32 and all(c in "0123456789abcdef" for c in e.id)
        assert e.name == "工坊"
        assert e.pos_key() == ("default", 1, 0)
        assert engine.get_entity(e.id) is e
        with pytest.raises(WorldError, match="地块不存在"):
            await engine.place_entity("workshop", "default", 99, 99)
        with pytest.raises(WorldError, match="不能为空"):
            await engine.place_entity("", "default", 1, 2)

    _run(_scenario(tmp_path, fn))


def test_remove_entity(tmp_path):
    """移除实体：级联清理背包；on_entity_removed 事件。"""

    async def fn(engine: V4WorldEngine, clock=None):
        removed = []
        engine.register_world_event(
            "on_entity_removed", lambda api, e: removed.append(e.id)
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        await engine.give_item(player.id, "apple", 3)
        assert engine.count_item(player.id, "apple") == 3
        await engine.remove_entity(player.id)
        assert engine.get_entity(player.id) is None
        assert engine.count_item(player.id, "apple") == 0
        assert removed == [player.id]
        with pytest.raises(WorldError, match="实体不存在"):
            await engine.remove_entity(player.id)

    _run(_scenario(tmp_path, fn))


# ---------- 物品原语 ----------


def test_inventory_primitives(tmp_path):
    """give/take/count/list：数量累计、不足不扣、个体 attrs。"""

    async def fn(engine: V4WorldEngine, clock=None):
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        assert await engine.give_item(player.id, "apple", 2) == 2
        assert await engine.give_item(player.id, "apple", 3) == 5
        assert engine.count_item(player.id, "apple") == 5
        assert await engine.take_item(player.id, "apple", 2) is True
        assert engine.count_item(player.id, "apple") == 3
        assert await engine.take_item(player.id, "apple", 99) is False
        assert engine.count_item(player.id, "apple") == 3
        # 个体差异（C1）
        await engine.give_item(player.id, "apple", 1, attrs={"shine": 9})
        inv = engine.list_inventory(player.id)
        apple = [i for i in inv if i["item_id"] == "apple"][0]
        assert apple["count"] == 4
        assert apple["def"]["name"] == "苹果"
        # 非法
        with pytest.raises(WorldError, match="物品不存在"):
            await engine.give_item(player.id, "nope", 1)
        with pytest.raises(WorldError, match="正整数"):
            await engine.give_item(player.id, "apple", 0)
        with pytest.raises(WorldError, match="实体不存在"):
            await engine.give_item("missing", "apple", 1)

    _run(_scenario(tmp_path, fn))


# ---------- 移动（路径 + 阻挡 + 传送） ----------


def test_move_identity_entity(tmp_path):
    """身份化实体路径移动（v3 语义）：广场 (0,0) 向北到步行街南街口。"""

    async def fn(engine: V4WorldEngine, clock=None):
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        scene = await engine.move(player.id, "up")
        assert (scene.map_id, scene.row, scene.col) == ("default", -1, 0)
        assert scene.location.name == "步行街·南街口"
        # 非身份化实体不能路径移动
        door = [e for e in engine.list_entities() if e.kind == "door"][0]
        with pytest.raises(WorldError, match="只有玩家/agent"):
            await engine.move(door.id, "up")
        # 方向非法
        with pytest.raises(WorldError, match="方向"):
            await engine.move(player.id, "north")

    _run(_scenario(tmp_path, fn))


def test_move_blocked_by_door(tmp_path):
    """block_move：木门（kind 声明）阻挡移动；开门（state 覆盖）后可通过。"""

    async def fn(engine: V4WorldEngine, clock=None):
        engine.register_entity_kind("door", block_move=True, interactions=("open",))
        player = await engine.place_entity("player", "default", 2, 0, name="小明")
        # (2,0) 老路 → 南 (3,0) 林间路口（木门在此）
        with pytest.raises(WorldError, match="挡住了"):
            await engine.move(player.id, "down")
        door = [e for e in engine.list_entities() if e.kind == "door"][0]
        await engine.set_state(door.id, {"open": True, "block_move": False})
        scene = await engine.move(player.id, "down")
        assert (scene.row, scene.col) == (3, 0)

    _run(_scenario(tmp_path, fn))


def test_move_entity_teleport(tmp_path):
    """move_entity：直接坐标（行为驱动），触发 on_entity_move/on_entity_enter。"""

    async def fn(engine: V4WorldEngine, clock=None):
        moves = []
        engine.register_world_event(
            "on_entity_move", lambda api, e, f, t: moves.append((e.id, f, t))
        )
        sign = [e for e in engine.list_entities() if e.kind == "sign"][0]
        await engine.move_entity(sign.id, "default", 0, 0)
        assert sign.pos_key() == ("default", 0, 0)
        assert moves[-1][2] == ("default", 0, 0)
        with pytest.raises(WorldError, match="地块不存在"):
            await engine.move_entity(sign.id, "default", 50, 50)

    _run(_scenario(tmp_path, fn))


# ---------- attrs / state ----------


def test_attrs_state_patch(tmp_path):
    """set_attrs/set_state 合并写；on_entity_changed 事件；重复实体不存在报错。"""

    async def fn(engine: V4WorldEngine, clock=None):
        changed = []
        engine.register_world_event(
            "on_entity_changed", lambda api, e, c: changed.append((e.id, c))
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        await engine.set_attrs(player.id, {"gold": 10})
        await engine.set_attrs(player.id, {"gold": 8, "level": 2})
        assert engine.get_attrs(player.id) == {"gold": 8, "level": 2}
        await engine.set_state(player.id, {"hungry": True})
        assert engine.get_state(player.id) == {"hungry": True}
        assert len(changed) == 3
        with pytest.raises(WorldError, match="实体不存在"):
            engine.get_attrs("missing")

    _run(_scenario(tmp_path, fn))


# ---------- 广播（B2） ----------


def test_say_scope_cell_unlimited(tmp_path):
    """cell 级说话无限制；on_say 事件带 scope。"""

    async def fn(engine: V4WorldEngine, clock=None):
        said = []
        engine.register_world_event(
            "on_say", lambda api, e, text, scope: said.append((e.id, text, scope))
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        await engine.say(player.id, "大家好")
        assert said[-1][1:] == ("大家好", "cell")
        with pytest.raises(WorldError, match="不能为空"):
            await engine.say(player.id, "  ")
        with pytest.raises(WorldError, match="scope"):
            await engine.say(player.id, "hi", scope="galaxy")

    _run(_scenario(tmp_path, fn))


def test_say_world_requires_megaphone_and_cooldown(tmp_path):
    """world 级广播：无喇叭报错；有喇叭消耗 1 个 + 30s 冷却；管理员豁免。"""

    async def fn(engine: V4WorldEngine, clock: FakeClock):
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        with pytest.raises(WorldError, match="喇叭"):
            await engine.say(player.id, "全图广播", scope="world")
        await engine.give_item(player.id, MEGAPHONE_ITEM_ID, 2)
        await engine.say(player.id, "第一次广播", scope="world")
        assert engine.count_item(player.id, MEGAPHONE_ITEM_ID) == 1
        with pytest.raises(WorldError, match="冷却"):
            await engine.say(player.id, "第二次广播", scope="world")
        clock.advance(BROADCAST_COOLDOWN_SECONDS + 1)
        await engine.say(player.id, "冷却结束", scope="world")
        assert engine.count_item(player.id, MEGAPHONE_ITEM_ID) == 0
        # 管理员豁免（无喇叭也可广播，无冷却）
        engine.admins.add(player.id)
        await engine.say(player.id, "管理员广播", scope="world")

    clock = FakeClock()
    _run(_scenario(tmp_path, fn, clock=clock))


# ---------- 交互（A1：effects 内核结算） ----------


def _demo_registry(engine: V4WorldEngine) -> None:
    engine.register_entity_kind(
        "merchant", interactions=("talk", "trade"), label="商贩"
    )
    engine.register_entity_kind("sign", interactions=("read",), label="告示牌")
    engine.register_interaction("talk", _talk_handler, label="打招呼")
    engine.register_interaction("trade", _trade_handler, label="看看货")
    engine.register_interaction("read", _read_handler, label="阅读")
    engine.register_interaction("buy", _buy_handler, label="买")


async def _talk_handler(api, req: InteractionRequest) -> InteractionResult:
    return InteractionResult(
        text=f"你好，我是{req.target.name}！",
        ui=None,
        effects=[],
    )


async def _trade_handler(api, req: InteractionRequest) -> InteractionResult:
    return InteractionResult(
        text="货单：苹果 5 金。",
        effects=[
            Effect("give_item", {"item_id": "apple", "count": 1}),
            Effect("set_attrs", {"patch": {"gold": -5}}),
        ],
    )


async def _read_handler(api, req: InteractionRequest) -> InteractionResult:
    return InteractionResult(text="告示牌上写着：明日集市。", effects=[])


async def _buy_handler(api, req: InteractionRequest) -> InteractionResult:
    return InteractionResult(text="买完了。", effects=[])


def test_interact_flow_and_effects(tmp_path):
    """交互全流程：可用动作（C3）→ handler → effects 结算（give/set_attrs）。"""

    async def fn(engine: V4WorldEngine, clock=None):
        _demo_registry(engine)
        merchant = [e for e in engine.list_entities() if e.kind == "merchant"][0]
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        # 可用动作 = kind 声明 ∪ 全局注册（C3）：merchant 声明 talk/trade，
        # 全局注册的 read/buy 对任何目标也可用
        actions = engine.available_actions(merchant.id)
        assert "talk" in actions and "trade" in actions
        assert "read" in actions and "buy" in actions

        result = await engine.interact(player.id, merchant.id, "talk")
        assert result.text == "你好，我是商贩·阿福！"
        # effects 结算
        result2 = await engine.interact(player.id, merchant.id, "trade")
        assert engine.count_item(player.id, "apple") == 1
        assert engine.get_attrs(player.id) == {"gold": -5}
        assert result2.text == "货单：苹果 5 金。"
        # 未声明且未注册的动作
        with pytest.raises(WorldError, match="没有"):
            await engine.interact(player.id, merchant.id, "fly")
        # 声明但未实现的动作
        engine.register_entity_kind("robot", interactions=("beep",))
        robot = await engine.place_entity("robot", "default", 1, 0, name="小机器人")
        with pytest.raises(WorldError, match="尚未实现"):
            await engine.interact(player.id, robot.id, "beep")

    _run(_scenario(tmp_path, fn))


def test_interact_use_item(tmp_path):
    """物品 use 交互：item_id 注入 + on_item_used 事件。"""

    async def fn(engine: V4WorldEngine, clock=None):
        # handler 需要 api：挂一个真实 WorlditorPlayAPI 实例（模拟 PlayLoader 绑定）
        from astrbot_plugin_worlditor.world.play.api import WorlditorPlayAPI

        engine.attach_play_api("test_play", WorlditorPlayAPI(engine, "test_play"))
        used = []
        engine.register_world_event(
            "on_item_used",
            lambda api, e, iid, count, args, result: used.append((e.id, iid, count)),
        )
        engine.register_interaction(
            "eat", _eat_handler, label="吃", play_id="test_play"
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        await engine.give_item(player.id, "apple", 2)
        result = await engine.interact(player.id, player.id, "eat", item_id="apple")
        assert "好吃" in result.text
        assert engine.count_item(player.id, "apple") == 1
        # count = on_item_used 触发时的持有数（handler 命令式扣减已生效）
        assert used[-1] == (player.id, "apple", 1)

    _run(_scenario(tmp_path, fn))


async def _eat_handler(api, req: InteractionRequest) -> InteractionResult:
    if api.count_item(req.entity_id, req.item_id) < 1:
        return InteractionResult(text="没有可吃的。")
    await api.take_item(req.entity_id, req.item_id, 1)
    return InteractionResult(text="咔嚓，好吃！")


def test_interact_handler_error_isolated(tmp_path):
    """handler 异常 → 转为可展示的 WorldError（不拖垮内核）。"""

    async def boom(api, req):
        raise RuntimeError("玩法包炸了")

    async def fn(engine: V4WorldEngine, clock=None):
        engine.register_interaction("boom", boom, label="自爆")
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        with pytest.raises(WorldError, match="交互执行出错"):
            await engine.interact(player.id, player.id, "boom")
        # 内核仍可用
        engine.register_interaction("ping", lambda api, req: _talk_handler(api, req))
        result = await engine.interact(player.id, player.id, "ping")
        assert result.text

    _run(_scenario(tmp_path, fn))


def test_interact_effects_reentrant(tmp_path):
    """effects 内的 move/move_entity/say 结算（可重入锁不死锁）。"""

    async def fn(engine: V4WorldEngine, clock=None):
        engine.register_entity_kind("teleporter", interactions=("activate",))
        engine.register_interaction(
            "activate",
            lambda api, req: _activate_handler(api, req),
            label="激活",
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        pod = await engine.place_entity("teleporter", "default", 1, 0, name="传送台")
        result = await engine.interact(player.id, pod.id, "activate")
        assert player.pos_key() == ("default", 5, 1)  # move_entity 生效
        assert "嗡嗡" in result.text  # say effect 已结算（文本来自 handler）

    _run(_scenario(tmp_path, fn))


async def _activate_handler(api, req: InteractionRequest) -> InteractionResult:
    return InteractionResult(
        text="传送台嗡嗡作响，你被传送到了迷雾深处。",
        effects=[
            Effect("move_entity", {"map_id": "default", "row": 5, "col": 1}),
            Effect("say", {"text": "一道光芒闪过！", "scope": "cell"}),
        ],
    )


def test_interact_invalid_effects(tmp_path):
    """非法 effects：未知 op / 缺参数 → WorldError。"""

    async def fn(engine: V4WorldEngine, clock=None):
        engine.register_interaction(
            "bad_op",
            lambda api, req: InteractionResult(effects=[Effect("teleport", {})]),
        )
        engine.register_interaction(
            "bad_give",
            lambda api, req: InteractionResult(effects=[Effect("give_item", {})]),
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        with pytest.raises(WorldError, match="未知交互效果"):
            await engine.interact(player.id, player.id, "bad_op")
        with pytest.raises(WorldError, match="缺少 item_id"):
            await engine.interact(player.id, player.id, "bad_give")

    _run(_scenario(tmp_path, fn))


def test_interact_take_item_effect_fails(tmp_path):
    """take_item effect 数量不足 → 结算失败报错。"""

    async def fn(engine: V4WorldEngine, clock=None):
        engine.register_interaction(
            "rob",
            lambda api, req: InteractionResult(
                effects=[Effect("take_item", {"item_id": "apple"})]
            ),
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        with pytest.raises(WorldError, match="物品不足"):
            await engine.interact(player.id, player.id, "rob")

    _run(_scenario(tmp_path, fn))


# ---------- 事件总线 / 世界日志 ----------


def test_events_and_world_log(tmp_path):
    """事件总线分发（含 handler 异常隔离）+ world_log 写入。"""

    async def fn(engine: V4WorldEngine, clock=None):
        seen = []
        engine.register_world_event("on_say", _bad_event_handler)
        engine.register_world_event(
            "on_say", lambda api, e, text, scope: seen.append(("say", e.id, text))
        )
        engine.register_world_event(
            "on_interact",
            lambda api, req, result: seen.append(("interact", req.action)),
        )
        engine.register_world_event(
            "on_entity_move",
            lambda api, e, f, t: seen.append(("move", e.id, f[0], f[1], f[2])),
        )
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        await engine.say(player.id, "hello")
        await engine.move(player.id, "up")
        assert ("say", player.id, "hello") in seen
        assert ("move", player.id, "default", 0, 0) in seen
        # world_log：say/move 已写入
        logs = await engine.store.list_world_log(limit=50)
        kinds = {log["kind"] for log in logs}
        assert "on_say" in kinds and "on_entity_move" in kinds
        assert all(log["data"].get("event") for log in logs)

    _run(_scenario(tmp_path, fn))


async def _bad_event_handler(api, *args):
    raise RuntimeError("事件 handler 炸了")


def test_world_log_capacity(tmp_path):
    """world_log 上限 5000 条（B3）：超限写入自动清理最旧。"""

    async def fn(engine: V4WorldEngine, clock=None):
        player = await engine.place_entity("player", "default", 0, 0, name="小明")
        for i in range(5050):
            await engine.say(player.id, f"msg-{i}")
        logs = await engine.store.list_world_log(limit=99999)
        assert len(logs) <= 5000
        assert logs[0]["data"]["values"][0] == "msg-5049"  # 最新保留
        # 最旧 50 条（msg-0..msg-49）已被清理
        oldest = {log["data"]["values"][0] for log in logs[-10:]}
        assert "msg-49" not in oldest and "msg-4" not in oldest

    _run(_scenario(tmp_path, fn))


# ---------- on_tick 调度（A3） ----------


def test_tick_schedule_intervals(tmp_path):
    """on_tick：各自间隔 + dt 语义 + 异常隔离（手动驱动 _tick_once）。"""

    async def fn(engine: V4WorldEngine, clock: FakeClock):
        runs = []
        engine.register_world_event(
            "on_tick", _tick_handler_factory(runs, "slow"), interval=3
        )
        engine.register_world_event(
            "on_tick", _tick_handler_factory(runs, "fast"), interval=1
        )
        engine.register_world_event("on_tick", _bad_tick_handler, interval=1)
        # t=1000（首次）：所有 handler 到期（last_run=0），dt = 各自 interval
        await engine._tick_once()
        assert runs == [("slow", 3.0), ("fast", 1.0)]
        # t=1001：fast 恰好 1s（>= interval）到期；slow 未到期
        clock.advance(1)
        await engine._tick_once()
        assert runs == [("slow", 3.0), ("fast", 1.0), ("fast", 1.0)]
        # t=1004：slow 到期 dt=4；fast 到期 dt=3
        clock.advance(3)
        await engine._tick_once()
        assert runs == [
            ("slow", 3.0),
            ("fast", 1.0),
            ("fast", 1.0),
            ("slow", 4.0),
            ("fast", 3.0),
        ]

    _run(_scenario(tmp_path, fn, clock=FakeClock(1000.0)))


def _tick_handler_factory(runs: list, tag: str):
    async def handler(api, dt: float):
        runs.append((tag, dt))

    return handler


async def _bad_tick_handler(api, dt: float):
    raise RuntimeError("tick 炸了")


# ---------- 地图编辑（B8：删除地块级联删实体） ----------


def test_delete_location_cascade(tmp_path):
    """删除地块：级联删其上实体 + 全图引用清理；有身份化实体在场拒绝。"""

    async def fn(engine: V4WorldEngine, clock=None):
        sign = [e for e in engine.list_entities() if e.kind == "sign"][0]
        # 把告示牌移到步行街南街口 (-1,0) 然后删除该地块
        await engine.move_entity(sign.id, "default", -1, 0)
        await engine.delete_location("default", -1, 0)
        assert engine.get_entity(sign.id) is None
        assert engine.get_location("default", -1, 0) is None
        # 引用清理：广场 (0,0) 的北向路径应已移除
        plaza = engine.get_location("default", 0, 0)
        assert plaza.connections["up"].paths == []
        # 有身份化实体在场拒绝
        player = await engine.place_entity("player", "default", 1, 0, name="小明")
        with pytest.raises(WorldError, match="无法删除"):
            await engine.delete_location("default", 1, 0)
        await engine.remove_entity(player.id)
        await engine.delete_location("default", 1, 0)

    _run(_scenario(tmp_path, fn))


def test_register_item_def_and_flush(tmp_path):
    """register_item_def（同步）→ flush_item_defs 落库；重启后仍在。"""

    async def fn(engine: V4WorldEngine, clock=None):
        engine.register_item_def(
            ItemDef(id="sword_01", name="木剑", desc="练习用木剑。", stackable=False)
        )
        await engine.flush_item_defs()
        assert "sword_01" in engine.store.items
        with pytest.raises(WorldError, match="物品 id 不能为空"):
            engine.register_item_def(ItemDef(id="", name="x"))

    _run(_scenario(tmp_path, fn))


# ---------- 界面扩展（B9：ui_hook before/after/replace） ----------


def test_apply_ui_hooks_positions(tmp_path):
    """ui_hook 三位置：before 插入 / after 追加 / replace 重写（递归展开）。"""

    from astrbot_plugin_worlditor.world.v4model import UiBlock

    async def fn(engine: V4WorldEngine, clock=None):
        # before / after
        engine.register_ui_hook(
            "text",
            "before",
            _hook_provider([UiBlock(kind="text", text="[前置]")]),
            play_id="p1",
        )
        engine.register_ui_hook(
            "text",
            "after",
            _hook_provider([UiBlock(kind="text", text="[后置]")]),
            play_id="p1",
        )
        out = await engine.apply_ui_hooks(UiBlock(kind="text", text="正文"))
        assert [b.text for b in out.blocks] == ["[前置]", "[后置]"]
        assert out.text == "正文"
        # replace：整体替换（provider 返回 custom 块）
        engine.register_ui_hook(
            "text",
            "replace",
            _hook_provider(
                [UiBlock(kind="custom", text="", data={"fallback_text": "自定义界面"})]
            ),
            play_id="p1",
        )
        out = await engine.apply_ui_hooks(UiBlock(kind="text", text="正文"))
        assert out.kind == "custom"
        # 嵌套：子块先递归展开
        engine.register_ui_hook(
            "list",
            "before",
            _hook_provider([UiBlock(kind="text", text="[列表头]")]),
            play_id="p1",
        )
        parent = UiBlock(kind="menu", blocks=[UiBlock(kind="list", text="条目")])
        out = await engine.apply_ui_hooks(parent)
        assert out.blocks[0].kind == "list"
        assert out.blocks[0].blocks[0].text == "[列表头]"
        # provider 异常隔离（不破坏渲染）
        engine.register_ui_hook(
            "menu", "after", _hook_provider(None, boom=True), play_id="p1"
        )
        out = await engine.apply_ui_hooks(UiBlock(kind="menu"))
        assert out is not None

    _run(_scenario(tmp_path, fn))


def _hook_provider(blocks: list | None, *, boom: bool = False):
    async def provider(api, block):
        if boom:
            raise RuntimeError("hook 炸了")
        return blocks or []

    return provider
