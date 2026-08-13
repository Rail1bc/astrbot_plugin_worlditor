"""SQLite 持久化层（aiosqlite，真异步）。

启动时全量载入内存（读路径快、免锁）；agent 位置写回 ``world_meta``；
``maps`` 表为空时幂等播种示例世界（小镇 + 迷雾区）。

表结构：
- ``maps(id TEXT PK, name, description_json, timezone, spawn_row, spawn_col)``
- ``locations(map_id, row, col, name, description_json, conns_json,
  PRIMARY KEY(map_id,row,col))``（conns_json 存 4 方向槽位配置，纯文本 JSON）
- ``templates(id TEXT PK, name, data_json)``（地块模板，复制预设）
- ``world_meta(key TEXT PK, value)``（schema 版本 + agent 位置 ``map_id,row,col``）

v3 无迁移：solo 迭代、未公开，旧 v2 库（locations/exits 表）数据直接丢弃；
``maps`` 为空即视为全新库，重新播种。

所有写操作由调用方（WorldEngine）在实例锁内执行，本类不自行加锁；
aiosqlite 连接内语句排队，写操作直接 ``await``，无需 to_thread。
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from .v3model import (
    Location,
    Target,
    WorldMap,
    WorldTemplate,
    location_to_dict,
    parse_location,
    parse_map,
    parse_text_schedule,
    target_to_dict,
)

SCHEMA_VERSION = "3"
DEFAULT_MAP_ID = "default"
AGENT_POS_KEY = "agent_position"


# ---------- 种子世界（示例小镇 + 迷雾区，幂等播种） ----------
# 新模型重建：平行路径（广场下方向两条 → 公园）、多目标加权（迷雾深处继续摸索前进
# 的主目标 + 小概率意外目标）、隐藏目标（迷雾森林"向左走"）、环路（森林→深处→空地→森林）、
# 非相邻连接（捷径 / 环路以任意坐标目标表达）。
# 坐标 (row, col)：up=行-1 / down=行+1 / left=列-1 / right=列+1。

_SEED_MAP = {
    "id": DEFAULT_MAP_ID,
    "name": "主世界",
    "description": "一片由你的想象力构成的世界。",
    "timezone": "Asia/Shanghai",
    "spawn_row": 0,
    "spawn_col": 0,
}

_SEED_LOCATIONS: list[dict] = [
    # (row, col, name, description, connections)
    {
        "row": 0,
        "col": 0,
        "name": "小镇广场",
        "description": "小镇的中心广场，人来人往。东西南北都有街道延伸出去。",
        "connections": {
            "up": [{"label": "沿着北街走向咖啡店", "targets": [{"row": -1, "col": 0}]}],
            "down": [
                {"label": "穿过南边的公园入口", "targets": [{"row": 1, "col": 0}]},
                {"label": "沿排水渠绕行（捷径）", "targets": [{"row": 1, "col": 0}]},
            ],
            "right": [
                {"label": "沿着东街走向图书馆", "targets": [{"row": 0, "col": 1}]}
            ],
            "left": [{"label": "走向西边的杂货店", "targets": [{"row": 0, "col": -1}]}],
        },
    },
    {
        "row": -1,
        "col": 0,
        "name": "街角咖啡店",
        "description": "飘着咖啡香的街角小店，暖黄的灯光透过玻璃窗。",
        "connections": {
            "down": [{"label": "回到广场", "targets": [{"row": 0, "col": 0}]}],
            "right": [
                {
                    "label": "沿着咖啡店后巷走向图书馆",
                    "targets": [{"row": 0, "col": 1}],
                }
            ],
        },
    },
    {
        "row": 1,
        "col": 0,
        "name": "中央公园",
        "description": {
            "periods": [
                {
                    "start": "06:00",
                    "end": "18:00",
                    "items": [
                        {
                            "text": "阳光穿过树荫洒落，长椅上的老人们正在下棋。",
                            "weight": 1,
                        }
                    ],
                },
                {
                    "start": "18:00",
                    "end": "06:00",
                    "items": [
                        {"text": "暮色四合，公园里只剩路灯昏黄的光。", "weight": 1}
                    ],
                },
            ]
        },
        "connections": {
            "up": [{"label": "回到广场", "targets": [{"row": 0, "col": 0}]}],
            "down": [{"label": "向南走进迷雾森林", "targets": [{"row": 2, "col": 0}]}],
        },
    },
    {
        "row": 0,
        "col": 1,
        "name": "老图书馆",
        "description": "静谧的图书馆，书架间弥漫着旧纸的气息。",
        "connections": {
            "left": [{"label": "回到广场", "targets": [{"row": 0, "col": 0}]}]
        },
    },
    {
        "row": 0,
        "col": -1,
        "name": "杂货店",
        "description": "堆满日用品的杂货店，老板正在柜台后打盹。",
        "connections": {
            "right": [{"label": "回到广场", "targets": [{"row": 0, "col": 0}]}],
            "down": [
                {
                    "label": "穿过杂货店后门的小巷（捷径）",
                    "targets": [{"row": 1, "col": 0}],
                }
            ],
        },
    },
    {
        "row": 2,
        "col": 0,
        "name": "迷雾森林",
        "description": "浓雾弥漫的森林，前后左右看起来都一模一样，令人迷失方向。",
        "connections": {
            "up": [
                {"label": "沿着来时的小路返回公园", "targets": [{"row": 1, "col": 0}]}
            ],
            "left": [
                {
                    "label": "向左走",
                    "reveal_target": False,
                    "targets": [{"row": 2, "col": 1}],
                }
            ],
            "right": [{"label": "向右走", "targets": [{"row": 2, "col": 1}]}],
            "down": [
                {"label": "拨开树丛向南摸索", "targets": [{"row": 3, "col": 1}]},
                {
                    "label": "沿着若隐若现的脚印前行",
                    "targets": [{"row": 3, "col": 1}],
                },
            ],
        },
    },
    {
        "row": 2,
        "col": 1,
        "name": "迷雾深处",
        "description": "雾更浓了，几乎看不清三米以外的任何东西。",
        "connections": {
            "down": [
                {
                    "label": "继续摸索前进",
                    "targets": [
                        {"row": 3, "col": 1, "weight": 1.0},
                        {"row": 2, "col": 0, "weight": 0.15},  # 脚下一滑跌回森林
                    ],
                }
            ],
            "left": [{"label": "转身往回走", "targets": [{"row": 2, "col": 0}]}],
        },
    },
    {
        "row": 3,
        "col": 1,
        "name": "迷雾空地",
        "description": "雾气在这里稍稍散开，露出一小片空地。但四周的雾墙依旧。",
        "connections": {
            "right": [{"label": "沿来路返回", "targets": [{"row": 2, "col": 0}]}]
        },
    },
]


def _seed_connections(conns: dict) -> dict:
    """把种子连接描述（方向 → 路径列表）转为 slot dict（v3model 格式）。"""
    out = {}
    for d in ("up", "right", "down", "left"):
        paths = conns.get(d, [])
        out[d] = {
            "direction": d,
            "enabled": bool(paths),
            "paths": [
                {
                    "label": (
                        parse_text_schedule(p["label"]).to_dict()
                        if p.get("label") is not None
                        else None
                    ),
                    "reveal_target": p.get("reveal_target", True),
                    "targets": [
                        target_to_dict(Target(map_id="", **t)) for t in p["targets"]
                    ],
                }
                for p in paths
            ],
        }
    return out


def _build_seed_locations() -> list[Location]:
    return [
        parse_location(
            {
                "map_id": DEFAULT_MAP_ID,
                "row": s["row"],
                "col": s["col"],
                "name": s["name"],
                "description": s["description"],
                "connections": _seed_connections(s.get("connections", {})),
            }
        )
        for s in _SEED_LOCATIONS
    ]


class WorldStore:
    """世界的 SQLite 持久化 + 内存态。

    内存态为启动时的全量快照：``maps`` / ``loc_by_pos`` / ``templates`` / ``agent_pos``。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self.maps: dict[str, WorldMap] = {}
        self.loc_by_pos: dict[tuple[str, int, int], Location] = {}
        self.templates: dict[str, WorldTemplate] = {}
        self.agent_pos: tuple[str, int, int] | None = None

    async def initialize(self) -> None:
        """打开连接、建表、幂等播种、全量载入内存、读取 agent 位置。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()
        await self._seed_if_empty()
        await self._load_all()
        await self._load_agent_pos()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS maps (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description_json TEXT,
                timezone TEXT,
                spawn_row INTEGER NOT NULL DEFAULT 0,
                spawn_col INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS locations (
                map_id TEXT NOT NULL,
                row INTEGER NOT NULL,
                col INTEGER NOT NULL,
                name TEXT NOT NULL,
                description_json TEXT,
                conns_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (map_id, row, col)
            );
            CREATE TABLE IF NOT EXISTS templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS world_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        await self._conn.commit()

    async def _seed_if_empty(self) -> None:
        """maps 为空 → 全新库，播种默认地图 + 种子地块 + agent 初始位置。"""
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM maps")
        row = await cur.fetchone()
        if row["n"] > 0:
            return
        await self._conn.execute(
            "INSERT INTO maps(id, name, description_json, timezone, spawn_row, spawn_col) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                _SEED_MAP["id"],
                _SEED_MAP["name"],
                json.dumps(
                    parse_text_schedule(_SEED_MAP["description"]).to_dict(),
                    ensure_ascii=False,
                ),
                _SEED_MAP["timezone"],
                _SEED_MAP["spawn_row"],
                _SEED_MAP["spawn_col"],
            ),
        )
        for loc in _build_seed_locations():
            await self._insert_location(loc)
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES(?, ?)",
            (
                AGENT_POS_KEY,
                f"{DEFAULT_MAP_ID},{_SEED_MAP['spawn_row']},{_SEED_MAP['spawn_col']}",
            ),
        )
        await self._conn.commit()

    async def _load_all(self) -> None:
        assert self._conn is not None
        self.maps = {}
        self.loc_by_pos = {}
        cur = await self._conn.execute("SELECT * FROM maps")
        for row in await cur.fetchall():
            m = parse_map(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": json.loads(row["description_json"])
                    if row["description_json"]
                    else None,
                    "timezone": row["timezone"],
                    "spawn_row": row["spawn_row"],
                    "spawn_col": row["spawn_col"],
                }
            )
            self.maps[m.id] = m
        cur = await self._conn.execute("SELECT * FROM locations")
        for row in await cur.fetchall():
            loc = parse_location(
                {
                    "map_id": row["map_id"],
                    "row": row["row"],
                    "col": row["col"],
                    "name": row["name"],
                    "description": json.loads(row["description_json"])
                    if row["description_json"]
                    else None,
                    "connections": json.loads(row["conns_json"] or "{}"),
                }
            )
            self.loc_by_pos[(loc.map_id, loc.row, loc.col)] = loc
        cur = await self._conn.execute("SELECT * FROM templates")
        for row in await cur.fetchall():
            self.templates[row["id"]] = WorldTemplate(
                id=row["id"],
                name=row["name"],
                data=json.loads(row["data_json"] or "{}"),
            )

    async def _load_agent_pos(self) -> None:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT value FROM world_meta WHERE key = ?", (AGENT_POS_KEY,)
        )
        row = await cur.fetchone()
        self.agent_pos = None
        if row:
            parts = row["value"].split(",")
            if len(parts) == 3:
                try:
                    self.agent_pos = (parts[0], int(parts[1]), int(parts[2]))
                except ValueError:
                    self.agent_pos = None

    async def save_agent_pos(self, map_id: str, row: int, col: int) -> None:
        """写回 agent 位置（跨对话持久化）。"""
        if self._conn is None:
            return
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES(?, ?)",
            (AGENT_POS_KEY, f"{map_id},{row},{col}"),
        )
        await self._conn.commit()
        self.agent_pos = (map_id, row, col)

    # ---------- 目标解析（死引用判定） ----------

    def resolve_target(self, t: Target, from_map_id: str) -> Target | None:
        """目标解析：map_id 空 = 当前地图；目标地图 / 地块不存在 → None（不可解析）。"""
        map_id = t.map_id or from_map_id
        if map_id not in self.maps:
            return None
        if (map_id, t.row, t.col) not in self.loc_by_pos:
            return None
        return Target(map_id=map_id, row=t.row, col=t.col, weight=t.weight)

    # ---------- 地图编辑写操作（DB 先、内存后，调用方持锁） ----------

    async def save_location(self, loc: Location) -> None:
        """写回 / 新建一个地块（整体替换对象）。"""
        assert self._conn is not None
        await self._insert_location(loc)
        self.loc_by_pos[(loc.map_id, loc.row, loc.col)] = loc

    async def _insert_location(self, loc: Location) -> None:
        assert self._conn is not None
        data = location_to_dict(loc)
        await self._conn.execute(
            "INSERT OR REPLACE INTO locations(map_id, row, col, name, description_json, conns_json) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                loc.map_id,
                loc.row,
                loc.col,
                loc.name,
                json.dumps(data["description"]) if data["description"] else None,
                json.dumps(data["connections"]),
            ),
        )
        await self._conn.commit()

    async def delete_location(self, map_id: str, row: int, col: int) -> None:
        """删除地块（引用清理由引擎负责）。"""
        assert self._conn is not None
        await self._conn.execute(
            "DELETE FROM locations WHERE map_id = ? AND row = ? AND col = ?",
            (map_id, row, col),
        )
        await self._conn.commit()
        self.loc_by_pos.pop((map_id, row, col), None)

    async def save_template(self, tpl: WorldTemplate) -> None:
        """写回 / 新建一个模板。"""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO templates(id, name, data_json) VALUES(?, ?, ?)",
            (tpl.id, tpl.name, json.dumps(tpl.data, ensure_ascii=False)),
        )
        await self._conn.commit()
        self.templates[tpl.id] = tpl

    async def delete_template(self, template_id: str) -> None:
        """删除一个模板。"""
        assert self._conn is not None
        await self._conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        await self._conn.commit()
        self.templates.pop(template_id, None)
