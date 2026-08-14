"""v4 持久化层（aiosqlite，真异步；与 v3 WorldStore 同库共存）。

v4 无迁移（设计决策）：v3 表（maps/locations/templates/world_meta）结构沿用、
数据共享；v4 新增五表（entities/items/inventories/play_data/world_log）在
**同一个 world.db 文件**中建表，与 v3 引擎互不干扰（v3 引擎无视 v4 表）。

启动时全量载入内存（读路径快、免锁），写操作由调用方（V4WorldEngine）在
实例锁内执行，本类不自行加锁。

表结构（DESIGN_V4.md「表结构」）：
- entities(id PK, map_id, row, col, kind, name, desc, user_id,
  attrs_json, state_json, last_active_ts) + idx_entities_pos
- items(id PK, name, desc, icon, stackable, use_action, attrs_json)
- inventories(entity_id, item_id, count, attrs_json, PK(entity_id,item_id))
- play_data(namespace, key, value_json, PK(namespace,key))
- world_log(id AUTOINCREMENT, ts, entity_id, kind, data_json) + idx_world_log_entity
  （容量上限 5000 条，写入时清理最旧，B3）

播种（幂等）：maps 空 → v3 种子世界（41 地块）；entities 空 → v4 种子实体
（商贩·阿福 / 告示牌 / 木门）；items 空 → v4 种子物品（苹果 / 喇叭）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from .identity import Account, TokenInfo
from .store import _SEED_MAP, DEFAULT_MAP_ID, _build_seed_locations
from .v3model import (
    Location,
    Target,
    WorldMap,
    WorldTemplate,
    parse_location,
    parse_map,
)
from .v4model import (
    Entity,
    InventoryEntry,
    ItemDef,
    entity_db_row,
    entity_from_row,
    item_db_row,
    item_from_row,
)

SCHEMA_VERSION = "4"
WORLD_LOG_LIMIT = 5000

# 内置广播道具（B2：say scope=world 消耗 1 个 + 每人 30s 冷却）
MEGAPHONE_ITEM_ID = "megaphone"

# ---------- v4 种子数据 ----------


def _seed_entities() -> list[Entity]:
    """v4 种子实体（B8：作为地图种子数据直接放置，静态）。

    - 广场「商贩·阿福」：kind=merchant，talk/trade（货单在 demo_play/data）
    - 步行街「告示牌」：kind=sign，read
    - 迷雾森林入口「木门」：kind=door，open，block_move（演示状态变更）
    """
    cells = [
        (
            DEFAULT_MAP_ID,
            0,
            0,
            "merchant",
            "商贩·阿福",
            "广场上的老商贩，货担里装着苹果和喇叭，笑眯眯地看着来往的行人。",
            {},
            {},
        ),
        (
            DEFAULT_MAP_ID,
            -2,
            0,
            "sign",
            "告示牌",
            "步行街边的木质告示牌，上面贴着几张纸。",
            {},
            {},
        ),
        (
            DEFAULT_MAP_ID,
            3,
            0,
            "door",
            "木门",
            "迷雾森林入口处一扇紧闭的木门，门缝里渗出丝丝凉意。",
            {},
            {"open": False},
        ),
    ]
    return [
        Entity(
            id=uuid.uuid4().hex,
            map_id=map_id,
            row=row,
            col=col,
            kind=kind,
            name=name,
            desc=desc,
            attrs=attrs,
            state=state,
        )
        for map_id, row, col, kind, name, desc, attrs, state in cells
    ]


def _seed_items() -> list[ItemDef]:
    """v4 种子物品：苹果（use_action 由 demo_play 注册）与喇叭（内置广播道具）。"""
    return [
        ItemDef(
            id="apple",
            name="苹果",
            desc="红彤彤的苹果，咬一口又脆又甜。",
            stackable=True,
            use_action="eat",
        ),
        ItemDef(
            id=MEGAPHONE_ITEM_ID,
            name="喇叭",
            desc="全图广播道具：向整个世界喊话一次（每人每 30 秒可用一次）。",
            stackable=True,
            use_action=None,
        ),
    ]


# ---------- v4 表 SQL ----------

_V4_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    map_id TEXT NOT NULL,
    row INTEGER NOT NULL,
    col INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    desc TEXT NOT NULL DEFAULT '',
    user_id TEXT,
    attrs_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    last_active_ts REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entities_pos ON entities(map_id, row, col);
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    desc TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '',
    stackable INTEGER NOT NULL DEFAULT 1,
    use_action TEXT,
    attrs_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS inventories (
    entity_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    attrs_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (entity_id, item_id)
);
CREATE TABLE IF NOT EXISTS play_data (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE TABLE IF NOT EXISTS world_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    entity_id TEXT,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_log_entity ON world_log(entity_id);
-- v4.1 身份（B13 自助注册 / B4 token 三档）
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    kind TEXT NOT NULL,
    account_id TEXT,
    username TEXT,
    created_ts REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invite_codes (
    code TEXT PRIMARY KEY,
    used INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL
);
"""

_V3_TABLES_SQL = """
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


class V4WorldStore:
    """v4 世界的 SQLite 持久化 + 内存态（启动时全量载入）。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        # 内存态快照
        self.maps: dict[str, WorldMap] = {}
        self.loc_by_pos: dict[tuple[str, int, int], Location] = {}
        self.templates: dict[str, WorldTemplate] = {}
        self.entities: dict[str, Entity] = {}
        self.items: dict[str, ItemDef] = {}
        self.inventories: dict[tuple[str, str], InventoryEntry] = {}
        self.play_data: dict[tuple[str, str], Any] = {}
        self.accounts: dict[str, Account] = {}
        self.tokens: dict[str, TokenInfo] = {}
        self.invite_codes: dict[str, dict] = {}

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        """打开连接、建表、幂等播种（v3 种子 + v4 种子）、全量载入内存。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_V3_TABLES_SQL)
        await self._conn.executescript(_V4_TABLES_SQL)
        await self._seed_if_empty()
        await self._load_all()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _seed_if_empty(self) -> None:
        """maps/entities/items 各自空则播种（幂等，互不依赖）。"""
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM maps")
        if (await cur.fetchone())["n"] == 0:
            await self._seed_world()
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM entities")
        if (await cur.fetchone())["n"] == 0:
            for entity in _seed_entities():
                await self._insert_entity(entity)
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM items")
        if (await cur.fetchone())["n"] == 0:
            for item in _seed_items():
                await self._insert_item(item)
        await self._conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        await self._conn.commit()

    async def _seed_world(self) -> None:
        """全新库：播种 v3 种子世界（41 地块，与 v3 引擎共享）。"""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO maps(id, name, description_json, timezone, spawn_row, spawn_col) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                _SEED_MAP["id"],
                _SEED_MAP["name"],
                json.dumps(_SEED_MAP["description"], ensure_ascii=False)
                if isinstance(_SEED_MAP["description"], str)
                else None,
                _SEED_MAP["timezone"],
                _SEED_MAP["spawn_row"],
                _SEED_MAP["spawn_col"],
            ),
        )
        for loc in _build_seed_locations():
            await self._insert_location(loc)
        await self._conn.commit()

    # ---------- 全量载入 ----------

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
        cur = await self._conn.execute("SELECT * FROM entities")
        for row in await cur.fetchall():
            entity = entity_from_row(row)
            if entity is not None:
                self.entities[entity.id] = entity
        cur = await self._conn.execute("SELECT * FROM items")
        for row in await cur.fetchall():
            item = item_from_row(row)
            if item is not None:
                self.items[item.id] = item
        cur = await self._conn.execute("SELECT * FROM inventories")
        for row in await cur.fetchall():
            self.inventories[(row["entity_id"], row["item_id"])] = InventoryEntry(
                item_id=row["item_id"],
                count=row["count"],
                attrs=json.loads(row["attrs_json"] or "{}"),
            )
        cur = await self._conn.execute("SELECT * FROM play_data")
        for row in await cur.fetchall():
            try:
                value = json.loads(row["value_json"])
            except (ValueError, TypeError):
                value = None
            self.play_data[(row["namespace"], row["key"])] = value
        cur = await self._conn.execute("SELECT * FROM accounts")
        for row in await cur.fetchall():
            self.accounts[row["id"]] = Account(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"],
                role=row["role"],
                created_ts=row["created_ts"],
            )
        cur = await self._conn.execute("SELECT * FROM tokens WHERE revoked = 0")
        for row in await cur.fetchall():
            self.tokens[row["token"]] = TokenInfo(
                token=row["token"],
                entity_id=row["entity_id"],
                tier=row["tier"],
                kind=row["kind"],
                account_id=row["account_id"],
                username=row["username"],
            )
        cur = await self._conn.execute("SELECT * FROM invite_codes")
        for row in await cur.fetchall():
            self.invite_codes[row["code"]] = {
                "used": bool(row["used"]),
                "created_ts": row["created_ts"],
            }

    # ---------- 目标解析（死引用判定，同 v3） ----------

    def resolve_target(self, t: Target, from_map_id: str) -> Target | None:
        """目标解析：map_id 空 = 当前地图；目标地图/地块不存在 → None（不可解析）。"""
        map_id = t.map_id or from_map_id
        if map_id not in self.maps:
            return None
        if (map_id, t.row, t.col) not in self.loc_by_pos:
            return None
        return Target(map_id=map_id, row=t.row, col=t.col, weight=t.weight)

    # ---------- 地图（v3 表写操作，与 v3 引擎共享数据） ----------

    async def save_map(self, m: WorldMap) -> None:
        """写回 / 新建一张地图。"""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO maps(id, name, description_json, timezone, spawn_row, spawn_col) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                m.id,
                m.name,
                json.dumps(m.description.to_dict()) if m.description else None,
                m.timezone,
                m.spawn_row,
                m.spawn_col,
            ),
        )
        await self._conn.commit()
        self.maps[m.id] = m

    async def save_location(self, loc: Location) -> None:
        """写回 / 新建一个地块（整体替换对象）。"""
        assert self._conn is not None
        await self._insert_location(loc)
        self.loc_by_pos[(loc.map_id, loc.row, loc.col)] = loc

    async def _insert_location(self, loc: Location) -> None:
        assert self._conn is not None
        from .v3model import location_to_dict

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

    # ---------- 实体 ----------

    async def save_entity(self, entity: Entity) -> None:
        """写回 / 新建一个实体（整体替换对象）。"""
        assert self._conn is not None
        await self._insert_entity(entity)
        self.entities[entity.id] = entity

    async def _insert_entity(self, entity: Entity) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO entities("
            "id, map_id, row, col, kind, name, desc, user_id, attrs_json, state_json, last_active_ts"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            entity_db_row(entity),
        )
        await self._conn.commit()

    async def delete_entity(self, entity_id: str) -> None:
        """删除实体并级联清理其背包。"""
        assert self._conn is not None
        await self._conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        await self._conn.execute(
            "DELETE FROM inventories WHERE entity_id = ?", (entity_id,)
        )
        await self._conn.commit()
        self.entities.pop(entity_id, None)
        self.inventories = {
            k: v for k, v in self.inventories.items() if k[0] != entity_id
        }

    # ---------- 物品 ----------

    async def save_item(self, item: ItemDef) -> None:
        """写回 / 新建一个物品定义。"""
        assert self._conn is not None
        await self._insert_item(item)
        self.items[item.id] = item

    async def _insert_item(self, item: ItemDef) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO items("
            "id, name, desc, icon, stackable, use_action, attrs_json"
            ") VALUES(?, ?, ?, ?, ?, ?, ?)",
            item_db_row(item),
        )
        await self._conn.commit()

    async def delete_item(self, item_id: str) -> None:
        """删除物品定义及其所有持有记录。"""
        assert self._conn is not None
        await self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        await self._conn.execute(
            "DELETE FROM inventories WHERE item_id = ?", (item_id,)
        )
        await self._conn.commit()
        self.items.pop(item_id, None)
        self.inventories = {
            k: v for k, v in self.inventories.items() if k[1] != item_id
        }

    # ---------- 背包 ----------

    async def set_inventory(
        self, entity_id: str, item_id: str, count: int, attrs: dict | None = None
    ) -> None:
        """整体替换一条持有记录；count<=0 删除该行。"""
        assert self._conn is not None
        key = (entity_id, item_id)
        if count <= 0:
            await self._conn.execute(
                "DELETE FROM inventories WHERE entity_id = ? AND item_id = ?",
                (entity_id, item_id),
            )
            self.inventories.pop(key, None)
        else:
            await self._conn.execute(
                "INSERT OR REPLACE INTO inventories(entity_id, item_id, count, attrs_json) "
                "VALUES(?, ?, ?, ?)",
                (
                    entity_id,
                    item_id,
                    count,
                    json.dumps(attrs or {}, ensure_ascii=False),
                ),
            )
            self.inventories[key] = InventoryEntry(
                item_id=item_id, count=count, attrs=attrs or {}
            )
        await self._conn.commit()

    # ---------- 玩法数据 KV（namespace 隔离） ----------

    async def set_play_kv(self, namespace: str, key: str, value: Any) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO play_data(namespace, key, value_json) VALUES(?, ?, ?)",
            (namespace, key, json.dumps(value, ensure_ascii=False)),
        )
        await self._conn.commit()
        self.play_data[(namespace, key)] = value

    # ---------- 世界日志（B3：上限 5000，写入时清理最旧） ----------

    async def append_world_log(
        self, ts: float, entity_id: str | None, kind: str, data: dict
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO world_log(ts, entity_id, kind, data_json) VALUES(?, ?, ?, ?)",
            (ts, entity_id, kind, json.dumps(data, ensure_ascii=False)),
        )
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM world_log")
        n = (await cur.fetchone())["n"]
        if n > WORLD_LOG_LIMIT:
            await self._conn.execute(
                "DELETE FROM world_log WHERE id IN ("
                "SELECT id FROM world_log ORDER BY id LIMIT ?)",
                (n - WORLD_LOG_LIMIT,),
            )
        await self._conn.commit()

    async def list_world_log(self, limit: int = 100) -> list[dict]:
        """读取世界日志（最新在前）。"""
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT id, ts, entity_id, kind, data_json FROM world_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = []
        for row in await cur.fetchall():
            try:
                data = json.loads(row["data_json"])
            except (ValueError, TypeError):
                data = {}
            rows.append(
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "entity_id": row["entity_id"],
                    "kind": row["kind"],
                    "data": data,
                }
            )
        return rows

    # ---------- 身份（v4.1：accounts / tokens / invite_codes） ----------

    async def save_account(self, account: Account) -> None:
        """写回 / 新建账户。"""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO accounts(id, username, password_hash, role, created_ts) "
            "VALUES(?, ?, ?, ?, ?)",
            (
                account.id,
                account.username,
                account.password_hash,
                account.role,
                account.created_ts,
            ),
        )
        await self._conn.commit()
        self.accounts[account.id] = account

    def get_account(self, account_id: str) -> Account | None:
        return self.accounts.get(account_id)

    def get_account_by_username(self, username: str) -> Account | None:
        for account in self.accounts.values():
            if account.username == username:
                return account
        return None

    async def save_token(self, info: TokenInfo, created_ts: float) -> None:
        """签发一份凭据（持久化 + 内存）。"""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO tokens("
            "token, entity_id, tier, kind, account_id, username, created_ts, revoked"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, 0)",
            (
                info.token,
                info.entity_id,
                info.tier,
                info.kind,
                info.account_id,
                info.username,
                created_ts,
            ),
        )
        await self._conn.commit()
        self.tokens[info.token] = info

    def get_token(self, token: str) -> TokenInfo | None:
        """解析未吊销凭据。"""
        return self.tokens.get(token)

    async def set_token_revoked(self, token: str, revoked: bool = True) -> bool:
        """吊销 / 恢复一份凭据；不存在返回 False。"""
        assert self._conn is not None
        if token not in self.tokens:
            return False
        await self._conn.execute(
            "UPDATE tokens SET revoked = ? WHERE token = ?",
            (1 if revoked else 0, token),
        )
        await self._conn.commit()
        if revoked:
            self.tokens.pop(token, None)
        return True

    async def revoke_tokens_of_account(self, account_id: str) -> None:
        """吊销某账户的全部凭据（登录/改密后旧凭据失效）。"""
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE tokens SET revoked = 1 WHERE account_id = ?", (account_id,)
        )
        await self._conn.commit()
        self.tokens = {
            t: v for t, v in self.tokens.items() if v.account_id != account_id
        }

    async def save_invite_code(self, code: str, created_ts: float) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT OR REPLACE INTO invite_codes(code, used, created_ts) VALUES(?, 0, ?)",
            (code, created_ts),
        )
        await self._conn.commit()
        self.invite_codes[code] = {"used": False, "created_ts": created_ts}

    def get_invite_code(self, code: str) -> dict | None:
        return self.invite_codes.get(code)

    def list_invite_codes(self) -> list[dict]:
        return [
            {"code": code, "used": entry["used"], "created_ts": entry["created_ts"]}
            for code, entry in self.invite_codes.items()
        ]

    async def set_invite_code_used(self, code: str, used: bool = True) -> bool:
        """标记邀请码已使用（消费 / 吊销）；不存在返回 False。"""
        assert self._conn is not None
        if code not in self.invite_codes:
            return False
        await self._conn.execute(
            "UPDATE invite_codes SET used = ? WHERE code = ?", (1 if used else 0, code)
        )
        await self._conn.commit()
        self.invite_codes[code]["used"] = used
        return True
