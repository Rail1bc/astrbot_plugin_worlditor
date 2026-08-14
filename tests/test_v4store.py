"""v4 存储层测试（v4store.py）：表结构、播种、CRUD、world_log 容量、v3 共存。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.store import WorldStore  # noqa: E402
from astrbot_plugin_worlditor.world.v4model import ItemDef  # noqa: E402
from astrbot_plugin_worlditor.world.v4store import (  # noqa: E402
    WORLD_LOG_LIMIT,
    V4WorldStore,
)


def _run(coro):
    return asyncio.run(coro)


async def _make_store(db_path: Path) -> V4WorldStore:
    store = V4WorldStore(db_path)
    await store.initialize()
    return store


def test_seed_tables(tmp_path):
    """v4 播种：v3 世界（41 地块）+ v4 实体/物品。"""

    async def fn():
        store = await _make_store(tmp_path / "world.db")
        try:
            assert len(store.loc_by_pos) == 41
            assert len(store.maps) == 1
            assert len(store.entities) == 3
            assert len(store.items) == 2
            assert "megaphone" in store.items
            assert "apple" in store.items
            # 索引生效（不报错即可）
            cur = await store._conn.execute(
                "SELECT COUNT(*) AS n FROM entities WHERE map_id=? AND row=? AND col=?",
                ("default", 0, 0),
            )
            assert (await cur.fetchone())["n"] == 1
        finally:
            await store.close()

    _run(fn())


def test_inventory_crud(tmp_path):
    """背包行：增/改/删（count<=0 删除）。"""

    async def fn():
        store = await _make_store(tmp_path / "world.db")
        try:
            await store.set_inventory("e1", "apple", 3)
            assert store.inventories[("e1", "apple")].count == 3
            await store.set_inventory("e1", "apple", 5, attrs={"shine": 1})
            assert store.inventories[("e1", "apple")].attrs == {"shine": 1}
            await store.set_inventory("e1", "apple", 0)
            assert ("e1", "apple") not in store.inventories
        finally:
            await store.close()

    _run(fn())


def test_play_data_kv(tmp_path):
    """玩法 KV：namespace 隔离、JSON 往返。"""

    async def fn():
        store = await _make_store(tmp_path / "world.db")
        try:
            await store.set_play_kv("ns1", "k", {"a": [1, 2, "x"]})
            await store.set_play_kv("ns2", "k", "other")
            assert store.play_data[("ns1", "k")] == {"a": [1, 2, "x"]}
            assert store.play_data[("ns2", "k")] == "other"
        finally:
            await store.close()

    _run(fn())


def test_world_log_capacity(tmp_path):
    """world_log 容量：超 5000 自动清理最旧。"""

    async def fn():
        store = await _make_store(tmp_path / "world.db")
        try:
            for i in range(WORLD_LOG_LIMIT + 50):
                await store.append_world_log(float(i), None, "on_say", {"n": i})
            logs = await store.list_world_log(limit=10**6)
            assert len(logs) == WORLD_LOG_LIMIT
            assert logs[0]["data"]["n"] == WORLD_LOG_LIMIT + 49  # 最新保留
            assert logs[-1]["data"]["n"] == 50  # 最旧 50 条已清
        finally:
            await store.close()

    _run(fn())


def test_coexist_with_v3_store(tmp_path):
    """与 v3 WorldStore 同库共存：v3 表共享、v4 表独立、互不干扰。"""

    async def fn():
        db = tmp_path / "world.db"
        v3 = WorldStore(db)
        await v3.initialize()
        v4 = V4WorldStore(db)
        await v4.initialize()
        try:
            # v3 视角：只有 v3 数据
            assert len(v3.loc_by_pos) == 41
            # v4 视角：v3 地块 + v4 实体/物品
            assert len(v4.loc_by_pos) == 41
            assert len(v4.entities) == 3
            # v3 写地图 → v4 内存不感知（过渡期已知限制，v4.1 统一）
            loc = v3.loc_by_pos[("default", 0, 0)]
            await v3.save_location(loc)  # 不报错即可
            # v4 写实体 → v3 不感知
            await v4.set_play_kv("ns", "k", 1)
            assert ("ns", "k") not in v3.__dict__.get("play_data", {})
        finally:
            await v4.close()
            await v3.close()

    _run(fn())


def test_delete_entity_cascades_inventory(tmp_path):
    """删除实体级联清理背包行。"""

    async def fn():
        store = await _make_store(tmp_path / "world.db")
        try:
            await store.set_inventory("e1", "apple", 2)
            await store.set_inventory("e2", "apple", 1)
            await store.delete_entity("e1")
            assert ("e1", "apple") not in store.inventories
            assert ("e2", "apple") in store.inventories
        finally:
            await store.close()

    _run(fn())


def test_item_crud(tmp_path):
    """物品定义：save/delete（删除级联清理持有）。"""

    async def fn():
        store = await _make_store(tmp_path / "world.db")
        try:
            await store.save_item(ItemDef(id="sword_01", name="木剑"))
            assert "sword_01" in store.items
            await store.set_inventory("e1", "sword_01", 1)
            await store.delete_item("sword_01")
            assert "sword_01" not in store.items
            assert ("e1", "sword_01") not in store.inventories
        finally:
            await store.close()

    _run(fn())
