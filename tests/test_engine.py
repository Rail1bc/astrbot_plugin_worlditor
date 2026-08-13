"""世界引擎 v3 单元测试。

需要 astrbot 包（插件运行时依赖）。以 namespace package 加载插件：
`sys.path.insert(0, REPO_ROOT.parent)` 后 `import astrbot_plugin_worlditor.*`
——插件模块用相对导入，必须按包加载（与 AstrBot 在 data/plugins 下加载插件一致）。
未安装 astrbot 时整组跳过。

时钟与 PRNG 全注入（engine 构造参数 ``clock`` / ``rand``），保证时间感知描述与
加权抽取确定。每个测试用 ``asyncio.run`` 起单循环，引擎初始化与终止在同一循环
内完成（aiosqlite 连接绑定创建它的事件循环）。
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

from astrbot_plugin_worlditor.world.engine import (  # noqa: E402
    AGENT_PLAYER_ID,
    WorldEngine,
    WorldError,
    scene_to_text,
)
from astrbot_plugin_worlditor.world.store import (  # noqa: E402
    DEFAULT_MAP_ID,
    WorldStore,
)
from astrbot_plugin_worlditor.world.v3model import Target  # noqa: E402

SH_TZ = ZoneInfo("Asia/Shanghai")
SEED_LOCATION_COUNT = 42


def fixed_clock(hour: int, minute: int = 0):
    return lambda: datetime(2026, 8, 13, hour, minute, tzinfo=SH_TZ)


def _run(coro):
    return asyncio.run(coro)


def make_engine(db_path: Path, clock=None, rand=None) -> WorldEngine:
    return WorldEngine(WorldStore(db_path), clock=clock or fixed_clock(12), rand=rand)


async def _scenario(tmp_path: Path, fn, rand=None):
    engine = make_engine(tmp_path / "world.db", rand=rand)
    await engine.initialize()
    try:
        return await fn(engine)
    finally:
        await engine.terminate()


# ---------- 播种与基础状态 ----------


def test_seed_and_map_load(tmp_path):
    """种子世界载入：42 地块、默认地图、agent 位于出生点 (0,0) 小镇广场。"""

    async def fn(engine: WorldEngine):
        assert len(engine.list_locations()) == SEED_LOCATION_COUNT
        maps = engine.list_maps()
        assert len(maps) == 1
        assert maps[0].id == DEFAULT_MAP_ID
        assert maps[0].name == "主世界"
        assert maps[0].timezone == "Asia/Shanghai"
        agent = engine.get_player(AGENT_PLAYER_ID)
        assert agent is not None
        assert agent.pos_key() == (DEFAULT_MAP_ID, 0, 0)
        plaza = engine.get_location(DEFAULT_MAP_ID, 0, 0)
        assert plaza is not None and plaza.name == "小镇广场"
        # 广场场景：up/down/left/right 各 1 条 = 4
        scene = await engine.describe_scene(AGENT_PLAYER_ID)
        assert scene is not None and len(scene.paths) == 4
        assert {p.direction for p in scene.paths} == {"up", "down", "right", "left"}

    _run(_scenario(tmp_path, fn))


def test_seed_idempotent(tmp_path):
    """幂等播种：重复初始化不重复插入。"""

    async def fn(engine: WorldEngine):
        await engine.terminate()
        engine2 = make_engine(tmp_path / "world.db")
        await engine2.initialize()
        try:
            assert len(engine2.list_locations()) == SEED_LOCATION_COUNT
        finally:
            await engine2.terminate()

    _run(_scenario(tmp_path, fn))


def test_register_player_and_scene(tmp_path):
    """注册人类玩家：返回起始地块，场景包含地块与路径。"""

    async def fn(engine: WorldEngine):
        loc = await engine.register_player("abc12345", name="测试者")
        assert (loc.map_id, loc.row, loc.col) == (DEFAULT_MAP_ID, 0, 0)
        scene = await engine.describe_scene("abc12345")
        assert scene is not None
        assert scene.location.name == "小镇广场"
        assert len(scene.paths) == 4
        data = scene.to_dict()
        assert data["location"]["name"] == "小镇广场"
        assert all(
            {"direction", "path", "label", "reveal_target", "target_name"} <= set(p)
            for p in data["paths"]
        )

    _run(_scenario(tmp_path, fn))


def test_register_default_name(tmp_path):
    """默认名回退：未给 name 时用 旅行者-<后四位>。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("a1b2c3d4")
        player = engine.get_player("a1b2c3d4")
        assert player is not None
        assert player.name == "旅行者-C3D4"

    _run(_scenario(tmp_path, fn))


def test_deregister_and_cleanup(tmp_path):
    """注销与超时清理：deregister 立即移除；清理只清超时的人类玩家，agent 永不清除。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345")
        assert engine.get_player("abc12345") is not None
        assert await engine.deregister_player("abc12345") is True
        assert engine.get_player("abc12345") is None
        assert await engine.deregister_player("abc12345") is False

        await engine.register_player("deadbeef")
        player = engine.get_player("deadbeef")
        player.last_active_ts -= 16 * 60
        removed = await engine._cleanup_idle_players()
        assert removed == 1
        assert engine.get_player("deadbeef") is None

        agent = engine.get_player(AGENT_PLAYER_ID)
        agent.last_active_ts -= 9999
        assert await engine._cleanup_idle_players() == 0
        assert engine.get_player(AGENT_PLAYER_ID) is not None
        assert await engine.deregister_player(AGENT_PLAYER_ID) is False

    _run(_scenario(tmp_path, fn))


def test_agent_position_persists_across_restart(tmp_path):
    """agent 位置持久化：移动后重建引擎（模拟重启），agent 仍在原地块。"""

    async def first_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        await engine.move(AGENT_PLAYER_ID, "up")  # 广场 → 步行街·南街口 (-1,0)
        assert engine.get_player(AGENT_PLAYER_ID).pos_key() == (
            DEFAULT_MAP_ID,
            -1,
            0,
        )
        await engine.terminate()

    async def second_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        try:
            assert engine.get_player(AGENT_PLAYER_ID).pos_key() == (
                DEFAULT_MAP_ID,
                -1,
                0,
            )
            assert engine.store.agent_pos == (DEFAULT_MAP_ID, -1, 0)
        finally:
            await engine.terminate()

    _run(first_run())
    _run(second_run())


# ---------- 移动 ----------


def test_move_by_direction(tmp_path):
    """按方向移动：玩家到达目标地块，场景随之更新。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345")
        scene = await engine.move("abc12345", "up")
        assert (scene.map_id, scene.row, scene.col) == (DEFAULT_MAP_ID, -1, 0)
        assert scene.location.name == "步行街·南街口"
        player = engine.get_player("abc12345")
        assert player.pos_key() == (DEFAULT_MAP_ID, -1, 0)
        back = await engine.move("abc12345", "down")
        assert (back.row, back.col) == (0, 0)

    _run(_scenario(tmp_path, fn))


def test_move_unknown_player_and_invalid_direction(tmp_path):
    """未注册玩家 / 非法方向报错；大道西尽头为死路。"""

    async def fn(engine: WorldEngine):
        with pytest.raises(WorldError):
            await engine.move("ghost", "up")
        await engine.register_player("abc12345")
        with pytest.raises(WorldError):
            await engine.move("abc12345", "diagonal")
        # 沿大道一直往西走到尽头 (0,-5)，再往西无路
        for _ in range(5):
            await engine.move("abc12345", "left")
        assert engine.get_player("abc12345").pos_key() == (DEFAULT_MAP_ID, 0, -5)
        with pytest.raises(WorldError, match="没有可走的路径"):
            await engine.move("abc12345", "left")

    _run(_scenario(tmp_path, fn))


def test_parallel_paths_require_index(tmp_path):
    """平行路径：给广场 right 槽配两条路径，不指定 path 报错；指定索引可达。"""

    async def fn(engine: WorldEngine):
        await engine.update_connection(
            DEFAULT_MAP_ID,
            0,
            0,
            "right",
            enabled=True,
            paths=[
                {"label": "沿着大道向东", "targets": [{"row": 0, "col": 1}]},
                {"label": "从后巷绕行", "targets": [{"row": 0, "col": 1}]},
            ],
        )
        await engine.register_player("abc12345")
        with pytest.raises(WorldError, match="多条路径"):
            await engine.move("abc12345", "right")
        scene = await engine.move("abc12345", "right", path=1)
        assert (scene.row, scene.col) == (0, 1)
        assert scene.location.name == "AstrBot大道"
        with pytest.raises(WorldError, match="路径索引"):
            await engine.move("abc12345", "left", path=3)

    _run(_scenario(tmp_path, fn))


def test_hidden_target_in_scene(tmp_path):
    """隐藏目标：reveal_target=False → target_name None（???）。"""

    async def fn(engine: WorldEngine):
        await engine.update_connection(
            DEFAULT_MAP_ID,
            0,
            0,
            "up",
            enabled=True,
            paths=[
                {
                    "label": "沿着幽暗小径摸索",
                    "reveal_target": False,
                    "targets": [{"row": 5, "col": 1}],
                }
            ],
        )
        await engine.register_player("abc12345", name="雾中人")
        scene = await engine.describe_scene("abc12345")
        up = next(p for p in scene.paths if p.direction == "up")
        assert up.label == "沿着幽暗小径摸索"
        assert up.reveal_target is False
        assert up.target_name is None
        text = scene_to_text(scene)
        assert "???" in text
        assert "AstrBot大道" in text  # right 目标可见
        moved = await engine.move("abc12345", "up")
        assert (moved.row, moved.col) == (5, 1)

    _run(_scenario(tmp_path, fn))


def test_move_weighted_accident(tmp_path):
    """多目标加权：给迷雾深处(5,1) down 配主目标(6,1,1.0) + 意外(5,0,0.15)。

    rand 注入：r*1.15 ≤ 1.0 → 主目标；> 1.0 → 意外。
    """

    async def to_depth(engine: WorldEngine):
        await engine.update_connection(
            DEFAULT_MAP_ID,
            5,
            1,
            "down",
            enabled=True,
            paths=[
                {
                    "label": "继续摸索前进",
                    "targets": [
                        {"row": 6, "col": 1, "weight": 1.0},
                        {"row": 5, "col": 0, "weight": 0.15},  # 脚下一滑跌回森林
                    ],
                }
            ],
        )
        await engine.register_player("abc12345")
        await engine.move("abc12345", "down")  # → 老路 (1,0)
        await engine.move("abc12345", "down")  # → 老路 (2,0)
        await engine.move("abc12345", "down")  # → 老路 (3,0)
        await engine.move("abc12345", "down")  # → 迷雾森林 (4,0)
        await engine.move("abc12345", "right")  # 森林无路 → 迷雾深处 (5,1)
        assert engine.get_player("abc12345").pos_key() == (DEFAULT_MAP_ID, 5, 1)

    async def main_path():
        engine = make_engine(tmp_path / "world.db", rand=lambda: 0.5)
        await engine.initialize()
        try:
            await to_depth(engine)
            scene = await engine.move("abc12345", "down")
            assert (scene.row, scene.col) == (6, 1)
        finally:
            await engine.terminate()

    async def accident():
        engine = make_engine(tmp_path / "world.db", rand=lambda: 0.9)
        await engine.initialize()
        try:
            await to_depth(engine)
            scene = await engine.move("abc12345", "down")
            assert (scene.row, scene.col) == (5, 0)  # 脚下一滑跌回森林
        finally:
            await engine.terminate()

    _run(main_path())
    _run(accident())


def test_move_explicit_target(tmp_path):
    """显式 target：直取路径目标坐标（确定性）；不在目标列表内报错。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345")
        await engine.move("abc12345", "down")  # → 老路 (1,0)
        await engine.move("abc12345", "down")  # → 老路 (2,0)
        await engine.move("abc12345", "down")  # → 老路 (3,0)
        await engine.move("abc12345", "down")  # → 迷雾森林 (4,0)
        scene = await engine.move("abc12345", "right", target={"row": 5, "col": 1})
        assert (scene.row, scene.col) == (5, 1)  # 迷雾深处
        with pytest.raises(WorldError, match="目标列表"):
            await engine.move("abc12345", "left", target={"row": 0, "col": 0})

    _run(_scenario(tmp_path, fn))


def test_dead_reference_path_hidden_and_untraversable(tmp_path):
    """死引用：主目标不存在 → 路径不展示 / 不可选；槽全死 → 视为禁用。"""

    async def fn(engine: WorldEngine):
        await engine.create_location(DEFAULT_MAP_ID, 20, 20, "断崖")
        await engine.update_connection(
            DEFAULT_MAP_ID,
            20,
            20,
            "down",
            enabled=True,
            paths=[{"targets": [{"row": 99, "col": 99}]}],
        )
        # 广场 right 槽替换为两条：一条通向 (20,20)，一条回到大道
        await engine.update_connection(
            DEFAULT_MAP_ID,
            0,
            0,
            "right",
            paths=[
                {"label": "走向断崖", "targets": [{"row": 20, "col": 20}]},
                {"label": "回到大道", "targets": [{"row": 0, "col": 1}]},
            ],
        )
        await engine.register_player("abc12345")
        await engine.move("abc12345", "right", path=0)
        assert engine.get_player("abc12345").pos_key() == (DEFAULT_MAP_ID, 20, 20)
        scene = await engine.describe_scene("abc12345")
        assert scene.paths == []  # down 槽启用但全部路径死 → 无任何可用路径
        with pytest.raises(WorldError, match="没有可走的路径"):
            await engine.move("abc12345", "down")
        with pytest.raises(WorldError, match="没有可走的路径"):
            await engine.move("abc12345", "down", path=0)

    _run(_scenario(tmp_path, fn))


def test_target_resolution(tmp_path):
    """目标可解析性：map_id 空 = 当前图；地图/地块不存在 → None。"""

    async def fn(engine: WorldEngine):
        store = engine.store
        assert store.resolve_target(Target(map_id="", row=0, col=0), DEFAULT_MAP_ID)
        assert (
            store.resolve_target(Target(map_id="ghost", row=0, col=0), DEFAULT_MAP_ID)
            is None
        )
        assert (
            store.resolve_target(Target(map_id="", row=99, col=99), DEFAULT_MAP_ID)
            is None
        )

    _run(_scenario(tmp_path, fn))


# ---------- 地图编辑 ----------


def test_create_location(tmp_path):
    """新建地块：成功、重复坐标报错、空名称 / 非法坐标报错。"""

    async def fn(engine: WorldEngine):
        loc = await engine.create_location(
            DEFAULT_MAP_ID, 5, 5, "新地块", description="一片新天地。"
        )
        assert (loc.map_id, loc.row, loc.col) == (DEFAULT_MAP_ID, 5, 5)
        assert loc.description is not None
        assert engine.get_location(DEFAULT_MAP_ID, 5, 5) is not None
        with pytest.raises(WorldError):
            await engine.create_location(DEFAULT_MAP_ID, 5, 5, "重复")
        with pytest.raises(WorldError):
            await engine.create_location(DEFAULT_MAP_ID, 6, 6, "   ")
        with pytest.raises(WorldError):
            await engine.create_location(DEFAULT_MAP_ID, "x", 6, "X")
        # 默认 4 槽全禁用
        loc = await engine.create_location(DEFAULT_MAP_ID, 9, 9, "空地")
        assert all(not s.enabled and s.paths == [] for s in loc.connections.values())

    _run(_scenario(tmp_path, fn))


def test_create_location_with_template(tmp_path):
    """以模板为蓝本建地块：空 name 沿用模板名，显式 name 覆盖，模板缺失报错。"""

    async def fn(engine: WorldEngine):
        await engine.create_template("tpl", "模板", map_id="", row=-1, col=0)
        loc = await engine.create_location(DEFAULT_MAP_ID, 8, 8, "", template_id="tpl")
        assert loc.name == "步行街·南街口"
        assert (
            loc.connections["down"].paths[0].targets[0].row,
            loc.connections["down"].paths[0].targets[0].col,
        ) == (
            9,
            8,
        )
        loc2 = await engine.create_location(
            DEFAULT_MAP_ID, 8, 9, "改名分店", template_id="tpl"
        )
        assert loc2.name == "改名分店"
        assert loc2.description == loc.description  # 连接/描述仍来自模板
        with pytest.raises(WorldError):
            await engine.create_location(DEFAULT_MAP_ID, 8, 10, "", template_id="ghost")
        # 重复坐标仍报错（带模板）
        with pytest.raises(WorldError):
            await engine.create_location(DEFAULT_MAP_ID, 8, 9, "", template_id="tpl")
        # 覆盖为空白 → 沿用模板名
        loc3 = await engine.create_location(
            DEFAULT_MAP_ID, 8, 12, "   ", template_id="tpl"
        )
        assert loc3.name == "步行街·南街口"

    _run(_scenario(tmp_path, fn))


def test_update_location(tmp_path):
    """更新地块：改 name/description、description 显式清空、不存在报错。"""

    async def fn(engine: WorldEngine):
        loc = await engine.update_location(DEFAULT_MAP_ID, 0, 0, name="小镇广场·新装")
        assert loc.name == "小镇广场·新装"
        assert engine.get_location(DEFAULT_MAP_ID, 0, 0).name == "小镇广场·新装"
        await engine.update_location(
            DEFAULT_MAP_ID, 0, 0, description="重新铺设的地砖。"
        )
        assert (
            engine.get_location(DEFAULT_MAP_ID, 0, 0).description.resolve(
                datetime(2026, 8, 13, 12, 0, tzinfo=SH_TZ)
            )
            == "重新铺设的地砖。"
        )
        await engine.update_location(DEFAULT_MAP_ID, 0, 0, description=None)
        assert engine.get_location(DEFAULT_MAP_ID, 0, 0).description is None
        with pytest.raises(WorldError):
            await engine.update_location(DEFAULT_MAP_ID, 9, 9, name="X")
        with pytest.raises(WorldError):
            await engine.update_location(DEFAULT_MAP_ID, 0, 0, name="")

    _run(_scenario(tmp_path, fn))


def test_time_aware_description(tmp_path):
    """分时段描述：小镇广场 06:00–18:00 白天 / 18:00–06:00 夜晚。"""

    async def day():
        engine = make_engine(tmp_path / "world_day.db", clock=fixed_clock(9))
        await engine.initialize()
        try:
            scene = await engine.describe_scene(AGENT_PLAYER_ID)
            assert "阳光" in scene.description
        finally:
            await engine.terminate()

    async def night():
        engine = make_engine(tmp_path / "world_night.db", clock=fixed_clock(21))
        await engine.initialize()
        try:
            scene = await engine.describe_scene(AGENT_PLAYER_ID)
            assert "夜色" in scene.description
        finally:
            await engine.terminate()

    _run(day())
    _run(night())


def test_delete_location_cascades(tmp_path):
    """删除地块：主目标被删 → 整条路径移除；意外目标被删 → 仅移除该目标。"""

    async def fn(engine: WorldEngine):
        # 给迷雾深处 (5,1) down 加意外目标 (4,0)，便于验证意外移除
        await engine.update_connection(
            DEFAULT_MAP_ID,
            5,
            1,
            "down",
            enabled=True,
            paths=[
                {
                    "label": "继续摸索前进",
                    "targets": [
                        {"row": 6, "col": 1, "weight": 1.0},
                        {"row": 4, "col": 0, "weight": 0.15},
                    ],
                }
            ],
        )
        await engine.delete_location(DEFAULT_MAP_ID, 4, 0)  # 迷雾森林
        assert engine.get_location(DEFAULT_MAP_ID, 4, 0) is None
        road = engine.get_location(DEFAULT_MAP_ID, 3, 0)
        assert road.connections["down"].paths == []  # 主目标 → 整条路径移除
        forest = engine.get_location(DEFAULT_MAP_ID, 5, 0)
        assert forest.connections["up"].paths == []  # 主目标 → 整条路径移除
        depth = engine.get_location(DEFAULT_MAP_ID, 5, 1)
        down = depth.connections["down"].paths[0]
        assert [(t.row, t.col) for t in down.targets] == [(6, 1)]  # 意外 (4,0) 移除
        with pytest.raises(WorldError):
            await engine.delete_location(DEFAULT_MAP_ID, 9, 9)

    _run(_scenario(tmp_path, fn))


def test_delete_location_rejects_occupied(tmp_path):
    """删除地块：拒绝删除 agent 或人类玩家所在地块。"""

    async def fn(engine: WorldEngine):
        with pytest.raises(WorldError, match="有玩家"):
            await engine.delete_location(DEFAULT_MAP_ID, 0, 0)  # agent 在广场
        await engine.register_player("abc12345")
        await engine.move("abc12345", "up")  # → 步行街·南街口 (-1,0)
        with pytest.raises(WorldError, match="有玩家"):
            await engine.delete_location(DEFAULT_MAP_ID, -1, 0)
        await engine.delete_location(DEFAULT_MAP_ID, 0, 1)  # AstrBot大道无玩家

    _run(_scenario(tmp_path, fn))


def test_move_location_rewrites_references(tmp_path):
    """移动地块：自身坐标迁移 + 全图引用重写 + 该地块上玩家跟随。"""

    async def fn(engine: WorldEngine):
        loc = await engine.move_location(DEFAULT_MAP_ID, -1, 0, 5, 5)  # 步行街·南街口
        assert (loc.row, loc.col) == (5, 5)
        plaza = engine.get_location(DEFAULT_MAP_ID, 0, 0)
        up = plaza.connections["up"].paths[0]
        assert (up.targets[0].row, up.targets[0].col) == (5, 5)  # 引用重写
        # 玩家在步行街 → 跟随
        await engine.register_player("abc12345")
        await engine.move("abc12345", "up")
        assert engine.get_player("abc12345").pos_key() == (DEFAULT_MAP_ID, 5, 5)
        # 占用格拒绝（老路 (1,0) 被占）
        with pytest.raises(WorldError, match="已被占用"):
            await engine.move_location(DEFAULT_MAP_ID, 0, 0, 1, 0)
        with pytest.raises(WorldError):
            await engine.move_location(DEFAULT_MAP_ID, 9, 9, 1, 0)

    _run(_scenario(tmp_path, fn))


def test_update_connection(tmp_path):
    """连接槽位：启用 + 整槽路径替换；隐藏目标；移动可达。"""

    async def fn(engine: WorldEngine):
        await engine.create_location(DEFAULT_MAP_ID, 7, 7, "瞭望塔")
        loc = await engine.update_connection(
            DEFAULT_MAP_ID,
            7,
            7,
            "down",
            enabled=True,
            paths=[
                {
                    "label": "下塔回镇",
                    "reveal_target": False,
                    "targets": [{"row": 0, "col": 0}],
                }
            ],
        )
        slot = loc.connections["down"]
        assert slot.enabled and len(slot.paths) == 1
        assert slot.paths[0].targets[0].row == 0
        await engine.register_player("abc12345")
        p = engine.get_player("abc12345")
        p.map_id, p.row, p.col = DEFAULT_MAP_ID, 7, 7
        scene = await engine.describe_scene("abc12345")
        down = [p for p in scene.paths if p.direction == "down"]
        assert len(down) == 1
        assert down[0].label == "下塔回镇"
        assert down[0].target_name is None  # 隐藏
        moved = await engine.move("abc12345", "down")
        assert (moved.row, moved.col) == (0, 0)
        with pytest.raises(WorldError, match="方向必须"):
            await engine.update_connection(DEFAULT_MAP_ID, 7, 7, "sideways")

    _run(_scenario(tmp_path, fn))


def test_edit_persists_across_restart(tmp_path):
    """编辑持久化：新建地块与连接在重建引擎后仍在。"""

    async def first_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        await engine.create_location(DEFAULT_MAP_ID, 9, 9, "沙滩")
        await engine.update_connection(
            DEFAULT_MAP_ID,
            9,
            9,
            "down",
            enabled=True,
            paths=[{"targets": [{"row": 0, "col": 0}]}],
        )
        await engine.terminate()

    async def second_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        try:
            loc = engine.get_location(DEFAULT_MAP_ID, 9, 9)
            assert loc is not None and loc.name == "沙滩"
            assert loc.connections["down"].enabled
        finally:
            await engine.terminate()

    _run(first_run())
    _run(second_run())


# ---------- 模板 ----------


def test_template_capture_and_apply(tmp_path):
    """模板：从步行街南街口捕获（同图目标转相对偏移），应用到空地块平移正确。"""

    async def fn(engine: WorldEngine):
        tpl = await engine.create_template(
            "street_tpl", "步行街模板", map_id="", row=-1, col=0
        )
        assert tpl.name == "步行街模板"
        down_t = tpl.data["connections"]["down"]["paths"][0]["targets"][0]
        right_t = tpl.data["connections"]["right"]["paths"][0]["targets"][0]
        assert down_t == {"dr": 1, "dc": 0, "weight": 1.0}  # → 广场
        assert right_t == {"dr": 0, "dc": 1, "weight": 1.0}  # → 知识库市场

        loc = await engine.apply_template("street_tpl", map_id="", row=10, col=10)
        assert (loc.map_id, loc.row, loc.col) == (DEFAULT_MAP_ID, 10, 10)
        assert loc.name == "步行街·南街口"
        down = loc.connections["down"].paths[0]
        assert (down.targets[0].row, down.targets[0].col) == (11, 10)
        right = loc.connections["right"].paths[0]
        assert (right.targets[0].row, right.targets[0].col) == (10, 11)

        with pytest.raises(WorldError, match="已被占用"):
            await engine.apply_template("street_tpl", map_id="", row=0, col=0)
        with pytest.raises(WorldError):
            await engine.apply_template("ghost", map_id="", row=3, col=3)

    _run(_scenario(tmp_path, fn))


def test_template_crud(tmp_path):
    """模板：重复 id / 改名 / 重新捕获 / 删除。"""

    async def fn(engine: WorldEngine):
        await engine.create_template("tpl", "模板", map_id="", row=0, col=0)
        with pytest.raises(WorldError):
            await engine.create_template("tpl", "重复", map_id="", row=0, col=1)
        tpl = await engine.update_template("tpl", name="广场模板")
        assert tpl.name == "广场模板"
        tpl2 = await engine.update_template("tpl", map_id="", row=-1, col=0)
        assert tpl2.data["connections"]["down"]["paths"][0]["targets"][0] == {
            "dr": 1,
            "dc": 0,
            "weight": 1.0,
        }
        with pytest.raises(WorldError):
            await engine.update_template("tpl", map_id="", row=-1)  # 缺 col
        await engine.delete_template("tpl")
        assert engine.get_template("tpl") is None
        with pytest.raises(WorldError):
            await engine.delete_template("tpl")

    _run(_scenario(tmp_path, fn))


def test_template_persists_across_restart(tmp_path):
    """模板持久化：创建模板后重建引擎，模板仍在并可应用。"""

    async def first_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        await engine.create_template("tpl", "模板", map_id="", row=0, col=0)
        await engine.terminate()

    async def second_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        try:
            tpl = engine.get_template("tpl")
            assert tpl is not None and tpl.name == "模板"
            loc = await engine.apply_template("tpl", map_id="", row=6, col=6)
            assert loc.name == "小镇广场"
        finally:
            await engine.terminate()

    _run(first_run())
    _run(second_run())
