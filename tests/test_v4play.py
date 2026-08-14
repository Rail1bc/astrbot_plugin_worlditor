"""玩法包加载器测试（DESIGN_V4.md「发现加载流程」）。

覆盖：demo_play 加载与行为集成、社区玩法包发现、namespace 隔离、
异常隔离（坏包不阻断）、版本校验、teardown。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.play import PlayLoader  # noqa: E402
from astrbot_plugin_worlditor.world.v4engine import (  # noqa: E402
    V4WorldEngine,
    WorldError,
)
from astrbot_plugin_worlditor.world.v4store import V4WorldStore  # noqa: E402

SEED_LOCATION_COUNT = 41


def _run(coro):
    return asyncio.run(coro)


def make_loader(db_path: Path, plays_dir: Path) -> PlayLoader:
    engine = V4WorldEngine(V4WorldStore(db_path))
    return PlayLoader(
        engine,
        plays_dir=plays_dir,
        demo_dir=REPO_ROOT / "demo_play",
        worlditor_version="4.0.0",
    )


def _write_play(root: Path, name: str, main_code: str, yaml_extra: str = "") -> Path:
    """构造一个社区玩法包目录。"""
    play_dir = root / name
    play_dir.mkdir(parents=True, exist_ok=True)
    (play_dir / "play.yaml").write_text(
        f"name: {name}\n"
        f"display_name: {name}\n"
        f"version: 0.1.0\n"
        f"author: test\n"
        f"requires:\n"
        f'  worlditor: ">=4.0.0"\n'
        f"  plays: []\n"
        f"{yaml_extra}",
        encoding="utf-8",
    )
    (play_dir / "main.py").write_text(main_code, encoding="utf-8")
    return play_dir


# ---------- demo_play 加载与行为集成 ----------


def test_demo_play_loaded(tmp_path):
    """demo_play 加载：注册表（kind/interaction/event）就位。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        plays = await loader.load_all()
        assert [p.play_id for p in plays] == ["worlditor_play_demo"]
        assert set(engine._kind_specs) == {"merchant", "sign", "door"}
        assert set(engine._interactions) >= {
            "talk",
            "trade",
            "read",
            "open",
            "eat",
            "bye",
        }
        # on_tick 带间隔订阅
        tick_bindings = engine._event_bindings["on_tick"]
        assert len(tick_bindings) == 1 and tick_bindings[0].interval == 5
        # 物品落库（flush 后持久化）
        await engine.terminate()
        engine2 = V4WorldEngine(V4WorldStore(db_path))
        await engine2.initialize()
        try:
            assert "apple" in engine2.store.items
        finally:
            await engine2.terminate()

    db_path = tmp_path / "world.db"
    loader = make_loader(db_path, tmp_path / "plays")
    engine = loader.engine
    _run(_async_main(engine, loader, fn))


async def _async_main(engine, loader, fn):
    await engine.initialize()
    try:
        return await fn(engine, loader)
    finally:
        await engine.terminate()


def test_demo_full_interaction_chain(tmp_path):
    """demo 全链路：talk → trade → buy（effects 结算）→ eat（事件回血）。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        await loader.load_all()
        merchant = [e for e in engine.list_entities() if e.kind == "merchant"][0]
        player = await engine.place_entity(
            "player", "default", 0, 0, name="小明", attrs={"gold": 20}
        )
        # talk
        result = await engine.interact(player.id, merchant.id, "talk")
        assert "阿福" in result.text
        assert result.ui is not None and result.ui.actions
        # trade → list
        result = await engine.interact(player.id, merchant.id, "trade")
        assert result.ui is not None and result.ui.kind == "list"
        # buy_apple：effects 结算（set_attrs 扣金 + give_item 苹果）
        result = await engine.interact(player.id, merchant.id, "buy_apple")
        assert engine.count_item(player.id, "apple") == 1
        assert engine.get_attrs(player.id)["gold"] == 15
        # 钱不够
        await engine.set_attrs(player.id, {"gold": 1})
        result = await engine.interact(player.id, merchant.id, "buy_apple")
        assert "钱不够" in result.text
        assert engine.count_item(player.id, "apple") == 1
        # buy_megaphone：命令式 API
        await engine.set_attrs(player.id, {"gold": 20})
        result = await engine.interact(player.id, merchant.id, "buy_megaphone")
        assert engine.count_item(player.id, "megaphone") == 1
        assert engine.get_attrs(player.id)["gold"] == 10
        # eat：use 交互 + on_item_used 回血（energy +1）
        await engine.give_item(player.id, "apple", 1)
        result = await engine.interact(player.id, player.id, "eat", item_id="apple")
        assert "咔嚓" in result.text
        assert engine.count_item(player.id, "apple") == 1
        assert engine.get_attrs(player.id).get("energy") == 1
        # read：kv 读写（namespace 隔离）
        sign = [e for e in engine.list_entities() if e.kind == "sign"][0]
        result = await engine.interact(player.id, sign.id, "read")
        assert "小镇公告" in result.text
        assert engine.kv_get("worlditor_play_demo", "bulletin_reads") == 1
        # open：门状态变更
        door = [e for e in engine.list_entities() if e.kind == "door"][0]
        result = await engine.interact(player.id, door.id, "open")
        assert "吱呀" in result.text
        assert door.state.get("open") is True
        assert door.state.get("block_move") is False
        result = await engine.interact(player.id, door.id, "open")
        assert "已经开着" in result.text

    _run(_play_scenario(tmp_path, fn))


async def _play_scenario(tmp_path, fn):
    db_path = tmp_path / "world.db"
    loader = make_loader(db_path, tmp_path / "plays")
    engine = loader.engine
    await engine.initialize()
    try:
        return await fn(engine, loader)
    finally:
        await engine.terminate()


def test_demo_door_blocks_and_enter_forest(tmp_path):
    """demo 门阻挡 + 进入迷雾提示（on_entity_enter 事件 + cell 说话）。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        await loader.load_all()
        player = await engine.place_entity("player", "default", 2, 0, name="小明")
        # 木门挡路（demo 注册 kind=door block_move）
        with pytest.raises(WorldError, match="挡住了"):
            await engine.move(player.id, "down")
        # 开门后可通行；进入 (4,0) 迷雾 → on_entity_enter 触发 cell 提示
        door = [e for e in engine.list_entities() if e.kind == "door"][0]
        await engine.interact(player.id, door.id, "open")
        await engine.move(player.id, "down")  # (3,0)
        await engine.move(player.id, "down")  # (4,0) 迷雾森林
        assert player.pos_key() == ("default", 4, 0)
        logs = await engine.store.list_world_log(limit=20)
        say_logs = [row for row in logs if row["kind"] == "on_say"]
        assert any("雾" in str(row["data"]) for row in say_logs)

    _run(_play_scenario(tmp_path, fn))


# ---------- 发现 / namespace 隔离 / 异常隔离 / 版本 ----------


def test_discover_community_plays(tmp_path):
    """发现：plays/ 下 worlditor_play_* 社区玩法包（非前缀目录忽略）。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        (tmp_path / "plays" / "not_a_play").mkdir(parents=True, exist_ok=True)
        plays = await loader.load_all()
        ids = [p.play_id for p in plays]
        assert "worlditor_play_demo" in ids
        assert "worlditor_play_hello" in ids
        assert "not_a_play" not in ids

    _write_play(
        tmp_path / "plays",
        "worlditor_play_hello",
        "def setup(api, context):\n    api.register_entity_kind('hello', label='你好')\n",
    )
    _run(_play_scenario(tmp_path, fn))


def test_namespace_isolation(tmp_path):
    """kv namespace 隔离：两个玩法包同 key 互不干扰。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        await loader.load_all()
        api_a = loader.plays["worlditor_play_demo"].api
        api_b = loader.plays["worlditor_play_kv"].api
        await api_a.kv_set("counter", 1)
        await api_b.kv_set("counter", 99)
        assert api_a.kv_get("counter") == 1
        assert api_b.kv_get("counter") == 99

    _write_play(
        tmp_path / "plays",
        "worlditor_play_kv",
        "def setup(api, context):\n    pass\n",
    )
    _run(_play_scenario(tmp_path, fn))


def test_bad_play_isolated(tmp_path):
    """异常隔离：main.py 抛异常的玩法包被跳过，不阻断 demo 与其他包。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        plays = await loader.load_all()
        ids = [p.play_id for p in plays]
        assert "worlditor_play_demo" in ids
        assert "worlditor_play_good" in ids
        assert "worlditor_play_broken" not in ids

    _write_play(
        tmp_path / "plays",
        "worlditor_play_broken",
        "raise RuntimeError('坏包')\n",
    )
    _write_play(
        tmp_path / "plays",
        "worlditor_play_good",
        "def setup(api, context):\n    api.register_entity_kind('good', label='好')\n",
    )
    _run(_play_scenario(tmp_path, fn))


def test_missing_play_yaml_skipped(tmp_path):
    """play.yaml 缺失的目录被跳过。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        plays = await loader.load_all()
        assert all(p.play_id != "worlditor_play_noyaml" for p in plays)

    (tmp_path / "plays" / "worlditor_play_noyaml").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plays" / "worlditor_play_noyaml" / "main.py").write_text(
        "def setup(api, context):\n    pass\n", encoding="utf-8"
    )
    _run(_play_scenario(tmp_path, fn))


def test_version_requirement_checked(tmp_path):
    """requires.worlditor 不兼容 → 跳过（v4.0 只校验 worlditor 版本）。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        plays = await loader.load_all()
        assert all(p.play_id != "worlditor_play_future" for p in plays)

    _write_play(
        tmp_path / "plays",
        "worlditor_play_future",
        "def setup(api, context):\n    pass\n",
        yaml_extra='  worlditor: ">=5.0.0"\n',
    )
    _run(_play_scenario(tmp_path, fn))


def test_unload_calls_teardown(tmp_path):
    """unload_all：调用 teardown(api)；api 引用解除。"""

    async def fn(engine: V4WorldEngine, loader: PlayLoader):
        await loader.load_all()
        assert "worlditor_play_demo" in loader.plays
        await loader.unload_all()
        assert loader.plays == {}
        assert engine._play_apis.get("worlditor_play_demo") is None

    _run(_play_scenario(tmp_path, fn))


def test_version_ok_unit():
    """版本比较（spec.version_ok）。"""
    from astrbot_plugin_worlditor.world.play.spec import version_ok

    assert version_ok("4.0.0", "*")
    assert version_ok("4.0.0", "")
    assert version_ok("4.0.0", ">=4.0.0")
    assert version_ok("4.0.1", ">=4.0.0")
    assert not version_ok("3.9.9", ">=4.0.0")
    assert version_ok("4.0.0", "==4.0.0")
    assert not version_ok("4.0.1", "==4.0.0")
    assert version_ok("4.5.0", "<5.0.0")
    assert version_ok("v4.0.0", ">=4.0.0")
    assert version_ok("4.0", ">=4.0.0")
    assert not version_ok("4.0.0-beta", ">=4.0.1")


def test_main_wires_v3_and_v4(tmp_path, monkeypatch):
    """main.py 装配：v3 + v4 引擎同库共存、demo 玩法包加载、terminate 干净。"""
    from astrbot_plugin_worlditor import main as main_mod
    from astrbot_plugin_worlditor.api import _ROUTES, _V4_ROUTES

    registered = []

    class FakeContext:
        def register_web_api(self, route, handler, methods, desc):
            registered.append((route, methods))

    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: tmp_path),
    )
    plugin = main_mod.WorlditorPlugin(FakeContext())
    assert len(registered) == len(_ROUTES) + len(_V4_ROUTES)  # v3 + v4 路由注册
    assert plugin.identity is not None  # v4.1 身份服务已装配
    _run(plugin.initialize())
    try:
        # v3 引擎：41 地块种子
        assert len(plugin.engine.list_locations()) == 41
        # v4 引擎：种子实体 + 物品 + demo 玩法包
        assert len(plugin.v4_engine.list_entities()) == 3
        assert "apple" in plugin.v4_engine.store.items
        assert "worlditor_play_demo" in plugin.play_loader.plays
        assert plugin.engine is not plugin.v4_engine
    finally:
        _run(plugin.terminate())
        assert plugin.play_loader.plays == {}
        assert plugin.v4_engine.store._conn is None  # v4 连接已关闭
        assert plugin.engine.store._conn is None  # v3 连接已关闭


def test_main_enable_world_api(tmp_path, monkeypatch):
    """enable_world_api=True：initialize 启动 HTTP 服务，terminate 停止。"""
    from astrbot_plugin_worlditor import main as main_mod

    started = []
    stopped = []

    class FakeServer:
        def __init__(self, app, *, host, port):
            self.host, self.port = host, port

        async def start(self):
            started.append((self.host, self.port))

        def stop(self):
            stopped.append(True)

    class FakeContext:
        def register_web_api(self, route, handler, methods, desc):
            pass

    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: tmp_path),
    )
    monkeypatch.setattr(main_mod, "WorldHttpServer", FakeServer)
    plugin = main_mod.WorlditorPlugin(
        FakeContext(),
        config={
            "enable_world_api": True,
            "world_api_host": "127.0.0.1",
            "world_api_port": 6288,
        },
    )
    _run(plugin.initialize())
    assert started == [("127.0.0.1", 6288)]
    assert plugin.mcp_server is not None  # MCP server 已构建
    _run(plugin.terminate())
    assert stopped == [True]
    assert plugin._http_task is None
