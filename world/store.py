"""SQLite 持久化层（aiosqlite，真异步）。

启动时全量载入内存（读路径快、免锁）；agent 位置写回 ``world_meta``；
``maps`` 表为空时幂等播种示例世界（广场 · 步行街 · AstrBot大道 ·
开源小区 · AstrBot大学 · 迷雾森林）。

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
    DIR_OFFSETS,
    DIRECTIONS,
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


# ---------- 种子世界（示例主世界，幂等播种） ----------
# 布局（单一默认地图；坐标 (row, col)：up=行-1 / down=行+1 / left=列-1 / right=列+1）：
# - (0,0) 小镇广场（出生点）：北连步行街、南连老路，东西为 AstrBot大道。
# - 步行街 (-1..-3, 0)：北端连 "开源" 小区（居民楼 + 街道，rows -4..-5）。
# - AstrBot大道 (0, ±1..±5)：两侧为商铺（插件市场 / Skill / 人格设定 / 知识库 /
#   TTS / T2I 市场、旧书店），商铺只与大道双向连接（不与老路 / 步行街 / 其他商铺
#   相连）；东端 (0,5) 连 AstrBot大学，西端 (0,-5) 为死路。
# - 老路 (1..3, 0)：南端没入迷雾森林 (4,0)/(5,0)/(5,1)/(6,1)。
# - 连接生成规则：相邻地块默认双向连接；商铺（avenue_only）只连 row 0 大道、非大道
#   地块也不连商铺；森林地块无相邻地块的方向 → 目标 (5,1)。

_SEED_MAP = {
    "id": DEFAULT_MAP_ID,
    "name": "主世界",
    "description": "由广场、步行街、AstrBot大道、开源小区与迷雾森林组成的世界。",
    "timezone": "Asia/Shanghai",
    "spawn_row": 0,
    "spawn_col": 0,
}

# 每个地块一个条目：(row, col, name, description, forest?)。
# 连接不在此处书写——由占位网格自动生成（见 _build_seed_locations）。
# 广场带分时段描述示例（06:00–18:00 白天 / 18:00–06:00 夜晚）。
_SEED_CELLS: list[dict] = [
    {
        "row": 0,
        "col": 0,
        "name": "小镇广场",
        "description": {
            "periods": [
                {
                    "start": "06:00",
                    "end": "18:00",
                    "items": [
                        {
                            "text": "阳光洒在广场中央的喷泉上，水花晶莹，人来人往。",
                            "weight": 1,
                        }
                    ],
                },
                {
                    "start": "18:00",
                    "end": "06:00",
                    "items": [
                        {
                            "text": "夜色渐浓，广场安静下来，只有路灯投下昏黄的光圈。",
                            "weight": 1,
                        }
                    ],
                },
            ]
        },
    },
    {
        "row": -1,
        "col": 0,
        "name": "步行街·南街口",
        "description": "步行街的南端入口，青石板路通向小镇广场，两旁是古旧的二层小楼。",
    },
    {
        "row": -2,
        "col": 0,
        "name": "步行街",
        "description": "青石板铺就的步行街，路边的梧桐投下浓荫，行人慢悠悠地走着。",
    },
    {
        "row": -3,
        "col": 0,
        "name": "步行街·北街尾",
        "description": "步行街的北端，再往前几步就是'开源'小区的大门。",
    },
    {
        "row": -4,
        "col": 0,
        "name": "开源小区·主街",
        "description": "开源小区的主街，两侧是灰白色的六层居民楼，楼下停着自行车。",
    },
    {
        "row": -4,
        "col": -1,
        "name": "开源小区·丁香苑",
        "description": "一栋爬满常青藤的居民楼，单元门前的花坛里丁香开得正盛。",
    },
    {
        "row": -4,
        "col": 1,
        "name": "开源小区·梧桐苑",
        "description": "一棵大梧桐遮住半栋楼，树荫下摆着几张石桌，有人在下棋。",
    },
    {
        "row": -5,
        "col": 0,
        "name": "开源小区·中心小广场",
        "description": "小区中心的小广场，健身器材边围着一群闲聊的老人。",
    },
    {
        "row": -5,
        "col": -1,
        "name": "开源小区·枫林苑",
        "description": "一排红色的居民楼，阳台上晾着五颜六色的衣服。",
    },
    {
        "row": -5,
        "col": 1,
        "name": "开源小区·银杏苑",
        "description": "楼下种着两排银杏，秋叶落满一地的时候一定很好看。",
    },
    {
        "row": 1,
        "col": 0,
        "name": "老路",
        "description": "一条踩得发亮的老土路，路边的野草足有半人高。",
    },
    {
        "row": 2,
        "col": 0,
        "name": "老路",
        "description": "老路渐渐没入树林，树冠遮住天光，空气变得潮湿。",
    },
    {
        "row": 3,
        "col": 0,
        "name": "老路·林间路口",
        "description": "老路在这里消失在一片浓雾弥漫的树林前，雾里有模糊的树影。",
    },
    {
        "row": 4,
        "col": 0,
        "name": "迷雾森林",
        "description": "浓雾从树林间涌出，脚下落叶沙沙，看不见三米以外。",
        "forest": True,
    },
    {
        "row": 5,
        "col": 0,
        "name": "迷雾森林",
        "description": "雾更浓了，四周的树木仿佛都长着一个样子。",
        "forest": True,
    },
    {
        "row": 5,
        "col": 1,
        "name": "迷雾深处",
        "description": "几乎伸手不见五指，东南西北在这里似乎没有意义。",
        "forest": True,
    },
    {
        "row": 6,
        "col": 1,
        "name": "迷雾深处",
        "description": "林子的最深处，四面全是雾墙，你感觉一直在原地打转。",
        "forest": True,
    },
    {
        "row": 0,
        "col": -5,
        "name": "AstrBot大道·西尽头",
        "description": "大道到这里戛然而止，正前方是一堵爬满爬山虎的老墙——这是条死路。",
    },
    {
        "row": 0,
        "col": -4,
        "name": "AstrBot大道",
        "description": "开阔的六车道大道，西段行人和车辆都少，路灯亮得整齐。",
    },
    {
        "row": 0,
        "col": -3,
        "name": "AstrBot大道",
        "description": "大道西段的中央有一个环岛花坛，花坛里的月季开得正好。",
    },
    {
        "row": 0,
        "col": -2,
        "name": "AstrBot大道",
        "description": "街道空旷安静，偶尔有一辆车驶过，卷起一阵风。",
    },
    {
        "row": 0,
        "col": -1,
        "name": "AstrBot大道",
        "description": "从这里开始，大道逐渐热闹起来，能听到远处的喧嚣。",
    },
    {
        "row": 0,
        "col": 1,
        "name": "AstrBot大道",
        "description": "大道两侧商铺林立，行人摩肩接踵，是镇里最热闹的地段。",
    },
    {
        "row": 0,
        "col": 2,
        "name": "AstrBot大道",
        "description": "各种招牌在阳光下闪闪发亮，叫卖声此起彼伏。",
    },
    {
        "row": 0,
        "col": 3,
        "name": "AstrBot大道",
        "description": "暮色里霓虹灯渐次亮起，大道上依然人流如织。",
    },
    {
        "row": 0,
        "col": 4,
        "name": "AstrBot大道",
        "description": "大道尽头隐约可见一座大学的红色校门。",
    },
    {
        "row": 0,
        "col": 5,
        "name": "AstrBot大道·东尽头",
        "description": "大道的东端，正前方是AstrBot大学气派的校门，进进出出都是学生。",
    },
    {
        "row": 1,
        "col": 2,
        "name": "Skill商店",
        "description": "店里挂着各种'技能'卷轴，店员说学会就能立刻上手。",
        "avenue_only": True,
    },
    {
        "row": 1,
        "col": 3,
        "name": "人格设定市场",
        "description": "一栋造型奇特的建筑，门口排队的人都在小声讨论'人设'方案。",
        "avenue_only": True,
    },
    {
        "row": -1,
        "col": 1,
        "name": "知识库市场",
        "description": "巨大的书库直通天花板，店员推着推车穿梭在书架间。",
        "avenue_only": True,
    },
    {
        "row": -1,
        "col": 2,
        "name": "TTS市场",
        "description": "店里传来各种合成嗓音的试听，有人在挑选'说话的声音'。",
        "avenue_only": True,
    },
    {
        "row": -1,
        "col": 3,
        "name": "T2I市场",
        "description": "橱窗里挂满色彩斑斓的画作，据说都是用'想象'生成的新画。",
        "avenue_only": True,
    },
    {
        "row": 1,
        "col": -1,
        "name": "插件市场",
        "description": "大道西侧的一个批发市场，堆满了各种二手插件和零件。",
        "avenue_only": True,
    },
    {
        "row": -1,
        "col": -1,
        "name": "旧书店",
        "description": "一家昏暗的旧书店，泛黄的书页散发着油墨香，老板在柜台后打盹。",
        "avenue_only": True,
    },
    {
        "row": 0,
        "col": 6,
        "name": "AstrBot大学·南门",
        "description": "校门气派，门楣上刻着'AstrBot大学'，新生和游客络绎不绝。",
    },
    {
        "row": 0,
        "col": 7,
        "name": "AstrBot大学·主教学楼",
        "description": "十层的教学楼，走廊里传来琅琅的读书声和敲键盘的声音。",
    },
    {
        "row": 0,
        "col": 8,
        "name": "AstrBot大学·图书馆",
        "description": "图书馆安静极了，只有翻书声和轻微的脚步声。",
    },
    {
        "row": 1,
        "col": 6,
        "name": "AstrBot大学·实验楼",
        "description": "实验楼里灯光彻夜不熄，隐约有机器运转的嗡嗡声。",
    },
    {
        "row": 1,
        "col": 7,
        "name": "AstrBot大学·宿舍区",
        "description": "一片学生宿舍楼，阳台上晾着五颜六色的衣服，楼下有小卖部。",
    },
    {
        "row": -1,
        "col": 6,
        "name": "AstrBot大学·食堂",
        "description": "饭点时分，食堂门口排起长队，飘出饭菜的香味。",
    },
    {
        "row": -1,
        "col": 7,
        "name": "AstrBot大学·操场",
        "description": "绿茵场上有人在踢球，跑道上是一圈圈慢跑的身影。",
    },
]

_DIR_LABELS = {"up": "北", "down": "南", "left": "西", "right": "东"}
_FOREST_SPECIAL_POS = (5, 1)  # 森林地块无相邻地块方向 → 都通向迷雾深处


def _default_path_label(loc_name: str, neighbor_name: str, direction: str) -> str:
    if neighbor_name != loc_name:
        return f"前往{neighbor_name}"
    return f"继续往{_DIR_LABELS[direction]}走"


def _seed_connections(conns: dict) -> dict:
    """把连接描述（方向 → 路径列表）转为 slot dict（v3model 格式）。"""
    out = {}
    for d in DIRECTIONS:
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
    """由占位网格自动生成连接：相邻地块默认双向；商铺只连大道（row 0）；
    森林无邻格方向 → 迷雾深处。"""
    cells: dict[tuple[int, int], dict] = {(s["row"], s["col"]): s for s in _SEED_CELLS}
    fr, fc = _FOREST_SPECIAL_POS
    out: list[Location] = []
    for (row, col), meta in cells.items():
        conns: dict[str, list] = {}
        for d in DIRECTIONS:
            dr, dc = DIR_OFFSETS[d]
            nr, nc = row + dr, col + dc
            if (nr, nc) in cells:
                neighbor = cells[(nr, nc)]
                # 商铺只与大道（row 0）相连；两侧都排除——非大道地块也不连商铺
                if meta.get("avenue_only") and nr != 0:
                    continue
                if neighbor.get("avenue_only") and row != 0:
                    continue
                conns[d] = [
                    {
                        "label": _default_path_label(meta["name"], neighbor["name"], d),
                        "targets": [{"row": nr, "col": nc}],
                    }
                ]
            elif meta.get("forest"):
                conns[d] = [
                    {
                        "label": "在浓雾中迷失方向，摸索着向前",
                        "targets": [{"row": fr, "col": fc}],
                    }
                ]
        out.append(
            parse_location(
                {
                    "map_id": DEFAULT_MAP_ID,
                    "row": row,
                    "col": col,
                    "name": meta["name"],
                    "description": meta.get("description"),
                    "connections": _seed_connections(conns),
                }
            )
        )
    return out


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
