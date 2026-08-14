"""v4 目标数据模型（见 DESIGN_V4.md）。

v4 引入的核心概念（v3 的 Location / WorldMap / WorldTemplate 沿用不变）：

- **实体**（B12）：世界唯一居民概念。玩家（kind="player"）、agent（kind="agent"）
  是内置身份化实体；其余 kind 由玩法包注册（merchant / sign / door ...），是
  地图编辑放置的布景与内容实体。所有实体统一 ``Entity`` 模型与 ``entities`` 表。
- **物品**：定义（ItemDef）与持有（inventories）分离；持有条目可带个体差异
  （attrs_json：强化等级、耐久等，C1）。
- **交互协议**：InteractionRequest / UiBlock / Effect / InteractionResult——
  玩法包只描述界面（UiBlock），内核按 schema 渲染；世界变更以声明式
  ``effects`` 返回，由内核结算（A1）。
- **事件表**：内核唯一事件总线（单一事件源），SSE 是它的序列化出口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------- 实体（世界唯一居民概念，B12） ----------


@dataclass
class Entity:
    """世界中的实体：玩家 / agent / 布景实体统一模型。

    attrs 为玩法数据（hp/exp/gold/equipped...），state 为实体状态
    （门开/关、库存、血量...），内核都不解释，由玩法包自管。
    """

    id: str  # uuid4 hex（B5）
    map_id: str
    row: int
    col: int
    kind: str  # player / agent（内置）或玩法包注册的 kind
    name: str
    desc: str = ""
    attrs: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    user_id: str | None = None  # 身份化实体：账户/实例标识（联邦预留）
    last_active_ts: float = 0.0  # 在线状态（动作/SSE 活动维护）

    def pos_key(self) -> tuple[str, int, int]:
        return (self.map_id, self.row, self.col)

    def is_identity(self) -> bool:
        """身份化实体（可认证绑定、有背包、位置持久化）。"""
        return self.kind in ("player", "agent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "map_id": self.map_id,
            "row": self.row,
            "col": self.col,
            "kind": self.kind,
            "name": self.name,
            "desc": self.desc,
            "attrs": self.attrs,
            "state": self.state,
            "user_id": self.user_id,
            "last_active_ts": self.last_active_ts,
        }

    @staticmethod
    def from_dict(value: Any) -> Entity | None:
        """从存储/API dict 容错解析实体；非法返回 None。"""
        if not isinstance(value, dict):
            return None
        for key in ("id", "map_id", "kind", "name"):
            if not isinstance(value.get(key), str) or not value[key]:
                return None
        row, col = value.get("row"), value.get("col")
        if (
            not isinstance(row, int)
            or isinstance(row, bool)
            or not isinstance(col, int)
            or isinstance(col, bool)
        ):
            return None
        return Entity(
            id=value["id"],
            map_id=value["map_id"],
            row=row,
            col=col,
            kind=value["kind"],
            name=value["name"],
            desc=value.get("desc") if isinstance(value.get("desc"), str) else "",
            attrs=value.get("attrs") if isinstance(value.get("attrs"), dict) else {},
            state=value.get("state") if isinstance(value.get("state"), dict) else {},
            user_id=value.get("user_id")
            if isinstance(value.get("user_id"), str)
            else None,
            last_active_ts=value.get("last_active_ts", 0.0)
            if isinstance(value.get("last_active_ts"), (int, float))
            else 0.0,
        )


# ---------- 物品（定义与持有分离） ----------


@dataclass
class ItemDef:
    """物品定义：由玩法包注册（register_item_def），持久化到 items 表。"""

    id: str  # uuid4 hex（B5）
    name: str
    desc: str = ""
    icon: str = ""  # 可选，后议（B1：UI 以名称 + kind 标签展示）
    stackable: bool = True
    use_action: str | None = (
        None  # 玩法包注册的 use 交互动作（如 "eat"/"craft"/"equip"）
    )
    attrs: dict = field(default_factory=dict)  # 玩法数据（价格/属性/配方钩子）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "desc": self.desc,
            "icon": self.icon,
            "stackable": self.stackable,
            "use_action": self.use_action,
            "attrs": self.attrs,
        }

    @staticmethod
    def from_dict(value: Any) -> ItemDef | None:
        if not isinstance(value, dict):
            return None
        for key in ("id", "name"):
            if not isinstance(value.get(key), str) or not value[key]:
                return None
        return ItemDef(
            id=value["id"],
            name=value["name"],
            desc=value.get("desc") if isinstance(value.get("desc"), str) else "",
            icon=value.get("icon") if isinstance(value.get("icon"), str) else "",
            stackable=value.get("stackable", True)
            if isinstance(value.get("stackable"), bool)
            else True,
            use_action=value.get("use_action")
            if isinstance(value.get("use_action"), str) and value["use_action"]
            else None,
            attrs=value.get("attrs") if isinstance(value.get("attrs"), dict) else {},
        )


@dataclass
class InventoryEntry:
    """实体背包中的一行：物品 + 数量 + 个体差异（C1）。"""

    item_id: str
    count: int
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "count": self.count, "attrs": self.attrs}


# ---------- 实体 kind 注册（玩法包扩展点） ----------


@dataclass
class EntityKindSpec:
    """玩法包注册的实体 kind 元数据（register_entity_kind）。

    block_move 为内核级物理规则（移动阻挡）；interactions 为该 kind 默认可用的
    动作名列表（C3：可用动作 = kind 声明 ∪ 全局注册表）；tick 为行为状态机开关
    （玩法包同时订阅 on_tick 驱动状态）。label 为 kind 标签文案（B1）。
    """

    kind: str
    block_move: bool = False
    interactions: tuple[str, ...] = ()
    tick: bool = False
    label: str = ""
    play_id: str = ""


# ---------- 交互协议（玩法与 UI 之间的契约） ----------


@dataclass
class InteractionRequest:
    """一次交互请求：发起者与目标都是实体（含自己，如物品 use）。"""

    entity_id: str  # 发起者（身份化实体）
    target: Entity | None  # 目标实体（含玩家/agent 实体，如查看角色卡）
    action: str
    args: dict = field(default_factory=dict)
    item_id: str | None = None  # 物品交互（use）


@dataclass
class MenuButton:
    """交互结果中的动作按钮：label 展示，action/args 为下一次交互。"""

    label: str
    action: str
    args: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "action": self.action, "args": self.args}


@dataclass
class UiBlock:
    """界面块：内核按 schema 渲染，玩法包不画界面。

    kind 取值（B1 / B9）：text / menu / form / list / confirm / character /
    custom。character 与 custom 的结构数据放 ``data``：
    - character: {"avatar": str, "attrs": [{"label", "value"}]}（角色卡）
    - custom: {"component": "namespace.name", "props": {...},
      "fallback_text": "..."}（自定义界面组件，MCP 侧取 fallback_text 降级）
    blocks 为子块（B9 界面钩子注入点；内核渲染时按序展开）。
    """

    kind: str
    title: str = ""
    text: str = ""
    fields: list[dict] = field(default_factory=list)  # form: {name,label,type,required}
    items: list[dict] = field(default_factory=list)  # list: {label,value,action?,args?}
    actions: list[MenuButton] = field(default_factory=list)
    data: dict = field(default_factory=dict)  # character/custom 附加结构
    blocks: list[UiBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "fields": self.fields,
            "items": self.items,
            "actions": [a.to_dict() for a in self.actions],
            "data": self.data,
            "blocks": [b.to_dict() for b in self.blocks],
        }


@dataclass
class Effect:
    """世界变更原语（内核结算，不信任玩法包直接改）。

    op 为引擎原语子集：give_item / take_item / move / move_entity / set_attrs /
    say（传送 = move_entity 特例，无独立 teleport）。
    """

    op: str
    args: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(value: Any) -> Effect | None:
        if not isinstance(value, dict):
            return None
        op = value.get("op")
        if not isinstance(op, str) or not op:
            return None
        args = value.get("args")
        return Effect(op=op, args=args if isinstance(args, dict) else {})


@dataclass
class InteractionResult:
    """交互结果：text 供 agent 消费，ui 供 WebUI 渲染，effects 由内核结算。"""

    text: str = ""
    ui: UiBlock | None = None
    effects: list[Effect] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "ui": self.ui.to_dict() if self.ui else None,
            "effects": [{"op": e.op, "args": e.args} for e in self.effects],
        }

    @staticmethod
    def from_dict(value: Any) -> InteractionResult | None:
        if not isinstance(value, dict):
            return None
        effects: list[Effect] = []
        for raw in value.get("effects") or []:
            effect = Effect.from_dict(raw)
            if effect is not None:
                effects.append(effect)
        return InteractionResult(
            text=value.get("text") if isinstance(value.get("text"), str) else "",
            ui=None,  # UiBlock 解析 v4.1（UI 协议层）落地
            effects=effects,
        )


# ---------- 事件表（内核唯一事件总线，单一事件源） ----------

# 事件名 → 订阅 handler 签名（均为 async，api 为 WorlditorPlayAPI 或 None）：
#   on_tick:           (api, dt)                          dt = 距上次执行秒数
#   on_entity_move:    (api, entity, from_pos, to_pos)
#   on_entity_enter:   (api, entity, map_id, row, col)
#   on_say:            (api, entity, text, scope)
#   on_interact:       (api, request, result)
#   on_item_used:      (api, entity, item_id, count, args, result)
#   on_entity_removed: (api, entity)
#   on_entity_changed: (api, entity, changed)
#   on_world_edited:   (api, what)
WORLD_EVENTS: tuple[str, ...] = (
    "on_tick",
    "on_entity_move",
    "on_entity_enter",
    "on_say",
    "on_interact",
    "on_item_used",
    "on_entity_removed",
    "on_entity_changed",
    "on_world_edited",
)

# 实体 id 序列化辅助（attrs/state JSON）


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def entity_db_row(entity: Entity) -> tuple:
    """entities 表写入行（与 v4store SQL 列序一致）。"""
    return (
        entity.id,
        entity.map_id,
        entity.row,
        entity.col,
        entity.kind,
        entity.name,
        entity.desc,
        entity.user_id,
        _dump_json(entity.attrs),
        _dump_json(entity.state),
        entity.last_active_ts,
    )


def entity_from_row(row: Any) -> Entity | None:
    """从 aiosqlite Row 解析实体（容错：json 损坏按空 dict）。"""
    return Entity.from_dict(
        {
            "id": row["id"],
            "map_id": row["map_id"],
            "row": row["row"],
            "col": row["col"],
            "kind": row["kind"],
            "name": row["name"],
            "desc": row["desc"],
            "user_id": row["user_id"],
            "attrs": _load_json(row["attrs_json"], {}),
            "state": _load_json(row["state_json"], {}),
            "last_active_ts": row["last_active_ts"],
        }
    )


def item_db_row(item: ItemDef) -> tuple:
    """items 表写入行。"""
    return (
        item.id,
        item.name,
        item.desc,
        item.icon,
        1 if item.stackable else 0,
        item.use_action,
        _dump_json(item.attrs),
    )


def item_from_row(row: Any) -> ItemDef | None:
    return ItemDef.from_dict(
        {
            "id": row["id"],
            "name": row["name"],
            "desc": row["desc"],
            "icon": row["icon"],
            "stackable": bool(row["stackable"]),
            "use_action": row["use_action"],
            "attrs": _load_json(row["attrs_json"], {}),
        }
    )


def _normalize_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def check_count(value: Any, what: str = "count") -> int:
    """校验正整数计数；非法抛 ValueError（供引擎转 WorldError）。"""
    count = _normalize_count(value)
    if count is None or count <= 0:
        raise ValueError(f"{what}必须是正整数")
    return count


__all__ = [
    "Effect",
    "Entity",
    "EntityKindSpec",
    "InteractionRequest",
    "InteractionResult",
    "InventoryEntry",
    "ItemDef",
    "MenuButton",
    "UiBlock",
    "WORLD_EVENTS",
    "check_count",
    "entity_db_row",
    "entity_from_row",
    "item_db_row",
    "item_from_row",
]
