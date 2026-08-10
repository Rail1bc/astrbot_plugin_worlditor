"""世界引擎单元测试。

需要 astrbot 包（插件运行时依赖）。以 namespace package 加载插件：
`sys.path.insert(0, REPO_ROOT.parent)` 后 `import astrbot_plugin_worlditor.*`
——插件模块用相对导入，必须按包加载（与 AstrBot 在 data/plugins 下加载插件一致）。
未安装 astrbot 时整组跳过。

每个测试用 `asyncio.run` 起单循环，引擎的初始化与终止在同一循环内完成
（aiosqlite 连接绑定创建它的事件循环，跨循环使用会报错）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
    AGENT_START_LOCATION,
    WorldStore,
)

SEED_LOCATION_COUNT = 8
SEED_EXIT_COUNT = 18


def _run(coro):
    return asyncio.run(coro)


def make_engine(db_path: Path) -> WorldEngine:
    return WorldEngine(WorldStore(db_path))


async def _scenario(tmp_path: Path, fn):
    engine = make_engine(tmp_path / "world.db")
    await engine.initialize()
    try:
        return await fn(engine)
    finally:
        await engine.terminate()


def test_seed_and_directed_graph_load(tmp_path):
    """种子地图载入：地块/出口数量正确，agent 位于起始地块。"""

    async def fn(engine: WorldEngine):
        assert len(engine.list_locations()) == SEED_LOCATION_COUNT
        assert len(engine.list_all_exits()) == SEED_EXIT_COUNT
        agent = engine.get_player(AGENT_PLAYER_ID)
        assert agent is not None
        assert agent.location_id == AGENT_START_LOCATION
        plaza = engine.get_location(AGENT_START_LOCATION)
        assert plaza is not None and plaza.name == "小镇广场"
        # 有向图：广场有 4 条出边（咖啡店/公园/图书馆/杂货店）
        assert len(engine.list_exits("town_plaza")) == 4

    _run(_scenario(tmp_path, fn))


def test_directed_edges_no_reciprocity(tmp_path):
    """有向性：捷径 a→b 存在但 b→a 不存在。"""

    async def fn(engine: WorldEngine):
        # 杂货店后门小巷：grocery→park 有，park→grocery 无
        grocery_ids = {e.id for e in engine.list_exits("town_grocery")}
        assert "town_grocery_park" in grocery_ids
        park_exits = engine.list_exits("town_park")
        assert not any(e.to_id == "town_grocery" for e in park_exits)
        assert "town_grocery_park" not in {e.id for e in park_exits}

    _run(_scenario(tmp_path, fn))


def test_register_player_and_scene(tmp_path):
    """注册人类玩家：返回起始地块，场景包含地块与出口。"""

    async def fn(engine: WorldEngine):
        loc = await engine.register_player("abc12345", name="测试者")
        assert loc.id == AGENT_START_LOCATION
        scene = await engine.describe_scene("abc12345")
        assert scene is not None
        assert scene.location.id == AGENT_START_LOCATION
        assert len(scene.exits) == 4
        data = scene.as_dict()
        assert data["location"]["name"] == "小镇广场"
        assert all({"exit_id", "label", "target_name"} <= set(e) for e in data["exits"])

    _run(_scenario(tmp_path, fn))


def test_register_default_name(tmp_path):
    """默认名回退：未给 name 时用 旅行者-<后四位>。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("a1b2c3d4")
        player = engine.get_player("a1b2c3d4")
        assert player is not None
        assert player.name == "旅行者-C3D4"

    _run(_scenario(tmp_path, fn))


def test_move_by_exit_id(tmp_path):
    """按 exit_id 移动：玩家到达目标地块，场景随之更新。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345")
        scene = await engine.move("abc12345", "town_plaza_cafe")
        assert scene.location.id == "town_cafe"
        assert scene.location.name == "街角咖啡店"
        player = engine.get_player("abc12345")
        assert player.location_id == "town_cafe"
        # 咖啡店可返回广场
        back = await engine.move("abc12345", "town_cafe_plaza")
        assert back.location.id == AGENT_START_LOCATION

    _run(_scenario(tmp_path, fn))


def test_move_invalid_exit(tmp_path):
    """非法出口：不存在的 exit_id / 不属于当前地块的出口均报错。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345")
        with pytest.raises(WorldError):
            await engine.move("abc12345", "no_such_exit")
        with pytest.raises(WorldError):
            # town_cafe_plaza 属于咖啡店的出边，玩家在广场时不可用
            await engine.move("abc12345", "town_cafe_plaza")

    _run(_scenario(tmp_path, fn))


def test_move_unknown_player(tmp_path):
    """未注册玩家移动报错。"""

    async def fn(engine: WorldEngine):
        with pytest.raises(WorldError):
            await engine.move("ghost", "town_plaza_cafe")

    _run(_scenario(tmp_path, fn))


def test_multi_edge_same_target(tmp_path):
    """多边同目标 + 隐藏目标：迷雾森林"向左走"与"向右走"通向同一地块，
    其中向左走 reveal_target=False（target_name 为 None）。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345", name="雾中人")
        await engine.move("abc12345", "town_plaza_park")  # 广场→公园
        await engine.move("abc12345", "town_park_forest")  # 公园→迷雾森林
        assert engine.get_player("abc12345").location_id == "mist_forest"
        scene = await engine.describe_scene("abc12345")
        exits = {e.exit_id: e for e in scene.exits}
        left = exits["mist_forest_left"]
        right = exits["mist_forest_right"]
        assert left.label == "向左走"
        assert right.label == "向右走"
        # 两个出口目标相同（都通向 mist_depth）
        assert engine.get_exit("mist_forest_left").to_id == "mist_depth"
        assert engine.get_exit("mist_forest_right").to_id == "mist_depth"
        # 隐藏目标：向左走不显示目标名
        assert left.target_name is None
        assert right.target_name == "迷雾深处"
        # 移动语义按 exit_id 区分：两条路都能到迷雾深处
        await engine.move("abc12345", "mist_forest_left")
        assert engine.get_player("abc12345").location_id == "mist_depth"

    _run(_scenario(tmp_path, fn))


def test_loop_returns_to_origin(tmp_path):
    """环路：迷雾森林 → 迷雾深处 → 迷雾空地 → 迷雾森林 走一圈回到原处。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345")
        await engine.move("abc12345", "town_plaza_park")
        await engine.move("abc12345", "town_park_forest")
        await engine.move("abc12345", "mist_forest_right")  # 迷雾森林→迷雾深处
        assert engine.get_player("abc12345").location_id == "mist_depth"
        await engine.move("abc12345", "mist_depth_forward")  # 迷雾深处→迷雾空地
        assert engine.get_player("abc12345").location_id == "mist_clearing"
        await engine.move("abc12345", "mist_clearing_back")
        assert engine.get_player("abc12345").location_id == "mist_forest"

    _run(_scenario(tmp_path, fn))


def test_deregister_and_cleanup(tmp_path):
    """注销与超时清理：deregister 立即移除；清理只清超时的人类玩家，agent 永不清除。"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345")
        assert engine.get_player("abc12345") is not None
        assert await engine.deregister_player("abc12345") is True
        assert engine.get_player("abc12345") is None
        # 重复注销无害
        assert await engine.deregister_player("abc12345") is False

        # 超时清理
        await engine.register_player("deadbeef")
        player = engine.get_player("deadbeef")
        player.last_active_ts -= 16 * 60  # 模拟 16 分钟前活跃
        removed = await engine._cleanup_idle_players()
        assert removed == 1
        assert engine.get_player("deadbeef") is None

        # agent 永不清除
        agent = engine.get_player(AGENT_PLAYER_ID)
        agent.last_active_ts -= 9999
        assert await engine._cleanup_idle_players() == 0
        assert engine.get_player(AGENT_PLAYER_ID) is not None

        # agent 不可注销
        assert await engine.deregister_player(AGENT_PLAYER_ID) is False

    _run(_scenario(tmp_path, fn))


def test_agent_position_persists_across_restart(tmp_path):
    """agent 位置持久化：移动后重建引擎（模拟重启），agent 仍在原地块。"""

    async def first_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        await engine.move(AGENT_PLAYER_ID, "town_plaza_cafe")
        assert engine.get_player(AGENT_PLAYER_ID).location_id == "town_cafe"
        await engine.terminate()

    async def second_run():
        engine = make_engine(tmp_path / "world.db")
        await engine.initialize()
        try:
            assert engine.get_player(AGENT_PLAYER_ID).location_id == "town_cafe"
            assert engine.store.agent_location_id == "town_cafe"
        finally:
            await engine.terminate()

    _run(first_run())
    _run(second_run())


def test_agent_position_initial_seed(tmp_path):
    """全新数据库：agent 初始位置为种子起始地块（world_meta 落盘）。"""

    async def fn(engine: WorldEngine):
        assert engine.store.agent_location_id == AGENT_START_LOCATION
        assert engine.get_player(AGENT_PLAYER_ID).location_id == AGENT_START_LOCATION

    _run(_scenario(tmp_path, fn))


def test_scene_text_hidden_target(tmp_path):
    """场景文本：隐藏目标显示 ???"""

    async def fn(engine: WorldEngine):
        await engine.register_player("abc12345", name="雾中人")
        await engine.move("abc12345", "town_plaza_park")
        await engine.move("abc12345", "town_park_forest")
        scene = await engine.describe_scene("abc12345")
        text = scene_to_text(scene)
        assert "迷雾森林" in text
        assert "向左走" in text
        assert "???" in text
        assert "向右走 → 迷雾深处" in text

    _run(_scenario(tmp_path, fn))


def test_seed_idempotent(tmp_path):
    """幂等播种：重复初始化不重复插入。"""

    async def fn(engine: WorldEngine):
        await engine.terminate()
        engine2 = make_engine(tmp_path / "world.db")
        await engine2.initialize()
        try:
            assert len(engine2.list_locations()) == SEED_LOCATION_COUNT
            assert len(engine2.list_all_exits()) == SEED_EXIT_COUNT
        finally:
            await engine2.terminate()

    _run(_scenario(tmp_path, fn))
