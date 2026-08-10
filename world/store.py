"""SQLite 持久化层（aiosqlite，真异步）。

启动时全量载入内存（读路径快、免锁）；agent 位置写回 ``world_meta``；
``locations`` 表为空时幂等播种示例小镇（小镇区 + 迷雾区）。

表结构：
- ``locations(id TEXT PK, name, description, layout_json)``
- ``exits(id TEXT PK, from_id, to_id, label, reveal_target, direction)``
  （from_id/to_id 引用 locations；同 (from,to) 允许多行——多条不同 label 出边；
  direction 为玩家视图十字槽位方向）
- ``world_meta(key TEXT PK, value)``（schema 版本 + ``agent_location``）

写操作除 agent 位置外，还包括地图编辑（增删改地块/出口），由调用方
（WorldEngine）在实例锁内执行，本类不自行加锁。

所有写操作由调用方（WorldEngine）在实例锁内执行，本类不自行加锁；
aiosqlite 连接内语句排队，写操作直接 ``await``，无需 to_thread。
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from .model import Exit, Location, parse_layout, serialize_layout

SCHEMA_VERSION = "2"
AGENT_LOCATION_KEY = "agent_location"
AGENT_START_LOCATION = "town_plaza"

# ---------- 种子地图（示例小镇，幂等播种） ----------
# 两区：小镇区（双向街道 + 单向捷径）与迷雾区（多边同目标 / 隐藏目标 / 环路）。
# 坐标为可视化提示；拓扑只由出边定义。

_SEED_LOCATIONS: list[tuple[str, str, str, float, float]] = [
    # (id, name, description, x, y)
    (
        "town_plaza",
        "小镇广场",
        "小镇的中心广场，人来人往。东西南北都有街道延伸出去。",
        100,
        150,
    ),
    (
        "town_cafe",
        "街角咖啡店",
        "飘着咖啡香的街角小店，暖黄的灯光透过玻璃窗。",
        280,
        60,
    ),
    ("town_park", "中央公园", "绿树成荫的公园，长椅上坐着悠闲的人们。", 280, 280),
    ("town_library", "老图书馆", "静谧的图书馆，书架间弥漫着旧纸的气息。", 460, 150),
    ("town_grocery", "杂货店", "堆满日用品的杂货店，老板正在柜台后打盹。", 470, 330),
    (
        "mist_forest",
        "迷雾森林",
        "浓雾弥漫的森林，前后左右看起来都一模一样，令人迷失方向。",
        680,
        90,
    ),
    ("mist_depth", "迷雾深处", "雾更浓了，几乎看不清三米以外的任何东西。", 880, 210),
    (
        "mist_clearing",
        "迷雾空地",
        "雾气在这里稍稍散开，露出一小片空地。但四周的雾墙依旧。",
        680,
        370,
    ),
]

_SEED_EXITS: list[tuple[str, str, str, str, bool, str]] = [
    # (id, from_id, to_id, label, reveal_target, direction)
    # direction 为玩家视图十字槽位方向；同一 from 的出边方向互异（编辑器规范）。
    # --- 小镇区：双向街道（成对反向出边） ---
    ("town_plaza_cafe", "town_plaza", "town_cafe", "沿着东街走向咖啡店", True, "up"),
    ("town_cafe_plaza", "town_cafe", "town_plaza", "回到广场", True, "left"),
    ("town_plaza_park", "town_plaza", "town_park", "穿过南边的公园入口", True, "down"),
    ("town_park_plaza", "town_park", "town_plaza", "回到广场", True, "left"),
    (
        "town_plaza_library",
        "town_plaza",
        "town_library",
        "沿着北街走向图书馆",
        True,
        "right",
    ),
    ("town_library_plaza", "town_library", "town_plaza", "回到广场", True, "left"),
    (
        "town_plaza_grocery",
        "town_plaza",
        "town_grocery",
        "走向东边的杂货店",
        True,
        "left",
    ),
    ("town_grocery_plaza", "town_grocery", "town_plaza", "回到广场", True, "left"),
    # --- 单向捷径（只有 a→b，无 b→a） ---
    (
        "town_grocery_park",
        "town_grocery",
        "town_park",
        "穿过杂货店后门的小巷（捷径）",
        True,
        "right",
    ),
    (
        "town_cafe_library",
        "town_cafe",
        "town_library",
        "沿着咖啡店后巷走向图书馆",
        True,
        "right",
    ),
    # --- 迷雾区入口 ---
    ("town_park_forest", "town_park", "mist_forest", "向东走进迷雾森林", True, "right"),
    (
        "mist_forest_park",
        "mist_forest",
        "town_park",
        "沿着来时的小路返回公园",
        True,
        "up",
    ),
    # --- 迷雾区：多边同目标（"向左走"/"向右走"通向同一目标） + 隐藏目标 ---
    ("mist_forest_left", "mist_forest", "mist_depth", "向左走", False, "left"),
    ("mist_forest_right", "mist_forest", "mist_depth", "向右走", True, "right"),
    # --- 迷雾区：环路（迷雾森林 → 迷雾深处 → 迷雾空地 → 迷雾森林） ---
    (
        "mist_forest_north",
        "mist_forest",
        "mist_clearing",
        "拨开树丛向北摸索",
        True,
        "down",
    ),
    ("mist_depth_forward", "mist_depth", "mist_clearing", "继续摸索前进", True, "up"),
    ("mist_depth_back", "mist_depth", "mist_forest", "转身往回走", True, "down"),
    ("mist_clearing_back", "mist_clearing", "mist_forest", "沿来路返回", True, "left"),
]


class WorldStore:
    """世界的 SQLite 持久化 + 内存态。

    内存态为启动时的全量快照：``locations`` / ``exits`` / ``exits_by_from``。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self.locations: dict[str, Location] = {}
        self.exits: dict[str, Exit] = {}
        self.exits_by_from: dict[str, list[Exit]] = {}
        self.agent_location_id: str | None = None

    async def initialize(self) -> None:
        """打开连接、建表、幂等播种、全量载入内存、读取 agent 位置。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._seed_if_empty()
        await self._load_all()
        await self._load_agent_location()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS locations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                layout_json TEXT
            );
            CREATE TABLE IF NOT EXISTS exits (
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL REFERENCES locations(id),
                to_id TEXT NOT NULL REFERENCES locations(id),
                label TEXT NOT NULL,
                reveal_target INTEGER NOT NULL DEFAULT 1,
                direction TEXT NOT NULL DEFAULT 'up'
            );
            CREATE TABLE IF NOT EXISTS world_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await self._migrate()
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        await self._conn.commit()

    async def _migrate(self) -> None:
        """老库（v1）增量迁移：exits 表补 direction 列。"""
        assert self._conn is not None
        cur = await self._conn.execute("PRAGMA table_info(exits)")
        columns = {row["name"] for row in await cur.fetchall()}
        if "direction" not in columns:
            await self._conn.execute(
                "ALTER TABLE exits ADD COLUMN direction TEXT NOT NULL DEFAULT 'up'"
            )
            await self._conn.commit()

    async def _seed_if_empty(self) -> None:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM locations")
        row = await cur.fetchone()
        if row["n"] > 0:
            return
        await self._conn.executemany(
            "INSERT INTO locations(id, name, description, layout_json) VALUES(?, ?, ?, ?)",
            [
                (loc_id, name, desc, serialize_layout(x, y))
                for loc_id, name, desc, x, y in _SEED_LOCATIONS
            ],
        )
        await self._conn.executemany(
            "INSERT INTO exits(id, from_id, to_id, label, reveal_target, direction) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            _SEED_EXITS,
        )
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES(?, ?)",
            (AGENT_LOCATION_KEY, AGENT_START_LOCATION),
        )
        await self._conn.commit()

    async def _load_all(self) -> None:
        assert self._conn is not None
        self.locations = {}
        self.exits = {}
        self.exits_by_from = {}
        cur = await self._conn.execute("SELECT * FROM locations")
        for row in await cur.fetchall():
            x, y = parse_layout(row["layout_json"])
            loc = Location(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                layout_x=x,
                layout_y=y,
            )
            self.locations[loc.id] = loc
        cur = await self._conn.execute("SELECT * FROM exits")
        for row in await cur.fetchall():
            exit_ = Exit(
                id=row["id"],
                from_id=row["from_id"],
                to_id=row["to_id"],
                label=row["label"],
                reveal_target=bool(row["reveal_target"]),
                direction=row["direction"],
            )
            self.exits[exit_.id] = exit_
            self.exits_by_from.setdefault(exit_.from_id, []).append(exit_)

    async def _load_agent_location(self) -> None:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT value FROM world_meta WHERE key = ?", (AGENT_LOCATION_KEY,)
        )
        row = await cur.fetchone()
        self.agent_location_id = row["value"] if row else None

    async def save_agent_location(self, location_id: str) -> None:
        """写回 agent 位置（跨对话持久化）。"""
        if self._conn is None:
            return
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES(?, ?)",
            (AGENT_LOCATION_KEY, location_id),
        )
        await self._conn.commit()
        self.agent_location_id = location_id

    # ---------- 地图编辑写操作（DB 先、内存后，调用方持锁） ----------

    async def save_location(self, loc: Location) -> None:
        """写回/新建一个地块（整体替换对象）。"""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO locations(id, name, description, layout_json) "
            "VALUES(?, ?, ?, ?)",
            (
                loc.id,
                loc.name,
                loc.description,
                serialize_layout(loc.layout_x, loc.layout_y),
            ),
        )
        await self._conn.commit()
        self.locations[loc.id] = loc

    async def delete_location_with_exits(self, location_id: str) -> None:
        """删除地块并级联删除所有以它为起点或终点的出边。"""
        assert self._conn is not None
        await self._conn.execute(
            "DELETE FROM exits WHERE from_id = ? OR to_id = ?",
            (location_id, location_id),
        )
        await self._conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
        await self._conn.commit()
        affected = [
            eid
            for eid, e in self.exits.items()
            if e.from_id == location_id or e.to_id == location_id
        ]
        for eid in affected:
            exit_ = self.exits.pop(eid)
            bucket = self.exits_by_from.get(exit_.from_id)
            if bucket is not None:
                self.exits_by_from[exit_.from_id] = [e for e in bucket if e.id != eid]
                if not self.exits_by_from[exit_.from_id]:
                    del self.exits_by_from[exit_.from_id]
        self.locations.pop(location_id, None)

    async def save_exit(self, exit_: Exit) -> None:
        """写回/新建一条出边（from_id 变更时维护旧桶）。"""
        assert self._conn is not None
        old = self.exits.get(exit_.id)
        await self._conn.execute(
            "INSERT OR REPLACE INTO exits(id, from_id, to_id, label, reveal_target, direction) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                exit_.id,
                exit_.from_id,
                exit_.to_id,
                exit_.label,
                int(exit_.reveal_target),
                exit_.direction,
            ),
        )
        await self._conn.commit()
        if old is not None:
            bucket = self.exits_by_from.get(old.from_id)
            if bucket is not None:
                self.exits_by_from[old.from_id] = [
                    e for e in bucket if e.id != exit_.id
                ]
                if not self.exits_by_from[old.from_id]:
                    del self.exits_by_from[old.from_id]
        self.exits[exit_.id] = exit_
        self.exits_by_from.setdefault(exit_.from_id, []).append(exit_)

    async def delete_exit(self, exit_id: str) -> None:
        """删除一条出边。"""
        assert self._conn is not None
        await self._conn.execute("DELETE FROM exits WHERE id = ?", (exit_id,))
        await self._conn.commit()
        exit_ = self.exits.pop(exit_id, None)
        if exit_ is not None:
            bucket = self.exits_by_from.get(exit_.from_id)
            if bucket is not None:
                self.exits_by_from[exit_.from_id] = [
                    e for e in bucket if e.id != exit_id
                ]
                if not self.exits_by_from[exit_.from_id]:
                    del self.exits_by_from[exit_.from_id]
