"""v3 目标数据模型（见 DESIGN.md「数据模型重构（v3 目标模型，规划中）」）。

已取代 v2 模型（world/model.py，已删除）：无迁移、solo 迭代，直接替换。

核心：
- 地块身份 = (map_id, 行, 列)；地图唯一，地块不唯一。
- 连接内嵌于地块：固定 4 方向槽位，每槽多条平行路径；路径内 targets 有序
  （首个 = 主目标 / 展示名，其余 = 意外路径加权随机）。
- 文本分时段加权（TextSchedule）：按当前时间命中时段，再按权重抽一条文本；
  地块描述 / 路径 label / 地图描述复用。
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DIRECTIONS = ("up", "right", "down", "left")

# 方向 ↔ 坐标偏移（行, 列）：up=行-1 / down=行+1 / left=列-1 / right=列+1。
# 与前端 pages/world/shared.js 的 DIR_OFFSETS 保持一致。
DIR_OFFSETS: dict[str, tuple[int, int]] = {
    "up": (-1, 0),
    "down": (1, 0),
    "left": (0, -1),
    "right": (0, 1),
}

OPPOSITE_DIR: dict[str, str] = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}

# 注入的随机源：返回 [0,1) 的均匀随机数（默认 random.random；测试注入定值）。
Rand = Callable[[], float]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_minutes(value: str) -> int:
    """把 "HH:MM" 解析为当日分钟数（0..1440）。"""
    try:
        h, m = value.split(":")
        minutes = int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        raise ValueError(f"无效的时段时间：{value!r}") from None
    if not 0 <= minutes <= 1440:
        raise ValueError(f"无效的时段时间：{value!r}")
    return minutes


def _period_matches(period: TextPeriod, minutes: int) -> bool:
    start = _parse_minutes(period.start)
    end = _parse_minutes(period.end)
    if end == 0:
        end = 1440  # 终点 00:00 视为当日 24:00
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end  # 跨午夜窗口


@dataclass
class TextItem:
    text: str
    weight: float = 1.0


@dataclass
class TextPeriod:
    start: str  # "HH:MM"，每日循环钟点窗口起点
    end: str  # "HH:MM"，终点（可跨午夜；00:00 视为当日 24:00）
    items: list[TextItem] = field(default_factory=list)


@dataclass
class TextSchedule:
    """分时段加权文本：取当前时间命中的时段，再按权重抽一条文本。

    归一化：缺省 = 单时段全天（00:00–24:00）+ 单条文本权重 1；
    重叠时段按列表顺序先命中者优先；无命中 / 无有效条目返回空串。
    """

    periods: list[TextPeriod] = field(
        default_factory=lambda: [TextPeriod("00:00", "24:00", [TextItem("", 1.0)])]
    )

    def resolve(self, now: datetime, rand: Rand | None = None) -> str:
        """返回当前时间命中的文本；无命中 / 无有效条目返回空串。"""
        minutes = now.hour * 60 + now.minute
        for period in self.periods:
            if not _period_matches(period, minutes):
                continue
            items = [it for it in period.items if it.text and it.weight > 0]
            if not items:
                return ""
            total = sum(it.weight for it in items)
            r = (rand() if rand else random.random()) * total
            acc = 0.0
            for it in items:
                acc += it.weight
                if r <= acc:
                    return it.text
            return items[-1].text
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "periods": [
                {
                    "start": p.start,
                    "end": p.end,
                    "items": [{"text": it.text, "weight": it.weight} for it in p.items],
                }
                for p in self.periods
            ]
        }


def parse_text_schedule(value: Any) -> TextSchedule:
    """把存储值解析为 TextSchedule。

    接受：None（返回默认空调度）、纯字符串（单时段全天单条）、
    {"periods": [...]} 结构。非法条目静默丢弃；全部非法 → 默认空调度。
    """
    if value is None:
        return TextSchedule()
    if isinstance(value, str):
        return TextSchedule(
            periods=[TextPeriod("00:00", "24:00", [TextItem(value, 1.0)])]
        )
    if not isinstance(value, dict):
        return TextSchedule()
    periods: list[TextPeriod] = []
    raw_periods = value.get("periods")
    if isinstance(raw_periods, list):
        for p in raw_periods:
            if not isinstance(p, dict):
                continue
            try:
                start = str(p.get("start") or "00:00")
                end = str(p.get("end") or "24:00")
                _parse_minutes(start)
                _parse_minutes(end)
            except ValueError:
                continue
            items: list[TextItem] = []
            raw_items = p.get("items")
            if isinstance(raw_items, list):
                for it in raw_items:
                    if not isinstance(it, dict):
                        continue
                    text = it.get("text")
                    if not isinstance(text, str) or not text:
                        continue
                    weight = it.get("weight", 1.0)
                    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                        continue
                    w = float(weight)
                    if not math.isfinite(w) or w <= 0:
                        continue
                    items.append(TextItem(text, w))
            if items:
                periods.append(TextPeriod(start, end, items))
    if not periods:
        return TextSchedule()
    return TextSchedule(periods=periods)


@dataclass
class Target:
    """一个目标坐标（地块引用）：map_id 空 = 当前地图；weight 为意外抽取权重。"""

    row: int
    col: int
    map_id: str = ""
    weight: float = 1.0


@dataclass
class ConnectionPath:
    """一条路径（可选出口）：label 为语义文本，targets 有序（首个=主目标，其余=意外）。"""

    label: TextSchedule | None = None
    reveal_target: bool = True
    targets: list[Target] = field(default_factory=list)


@dataclass
class ConnectionSlot:
    """固定方向槽位：enabled 为总开关；paths 多条 = 平行可选路径。"""

    direction: str
    enabled: bool = False
    paths: list[ConnectionPath] = field(default_factory=list)


@dataclass
class Location:
    """地块：身份 = (map_id, row, col)；connections 固定键 up/right/down/left。"""

    map_id: str
    row: int
    col: int
    name: str
    description: TextSchedule | None = None
    connections: dict[str, ConnectionSlot] = field(default_factory=dict)

    def offset(self, direction: str) -> Target:
        """该地块向 direction 偏移 1 的相邻目标。"""
        dr, dc = DIR_OFFSETS[direction]
        return Target(row=self.row + dr, col=self.col + dc)


@dataclass
class WorldMap:
    """地图：地图唯一，地块不唯一。timezone 为地图级时区，None = 服务器本地。"""

    id: str
    name: str
    description: TextSchedule | None = None
    timezone: str | None = None
    spawn_row: int = 0
    spawn_col: int = 0


# ---------- 序列化 / 解析（存储与 API 用；非法条目尽可能容错丢弃） ----------


def target_to_dict(t: Target) -> dict[str, Any]:
    d: dict[str, Any] = {"row": t.row, "col": t.col, "weight": t.weight}
    if t.map_id:
        d["map_id"] = t.map_id
    return d


def _norm_weight(value: Any) -> float:
    """权重归一化：非数字 / 布尔 / 非正 / 非有限数 → 1.0。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    w = float(value)
    return w if math.isfinite(w) and w > 0 else 1.0


def parse_target(value: Any) -> Target | None:
    if not isinstance(value, dict):
        return None
    row = value.get("row")
    col = value.get("col")
    if not _is_int(row) or not _is_int(col):
        return None
    map_id = value.get("map_id")
    return Target(
        map_id=str(map_id) if isinstance(map_id, str) else "",
        row=row,
        col=col,
        weight=_norm_weight(value.get("weight", 1.0)),
    )


def path_to_dict(p: ConnectionPath) -> dict[str, Any]:
    d: dict[str, Any] = {"reveal_target": p.reveal_target}
    if p.label:
        d["label"] = p.label.to_dict()
    d["targets"] = [target_to_dict(t) for t in p.targets]
    return d


def parse_path(value: Any) -> ConnectionPath:
    if not isinstance(value, dict):
        return ConnectionPath()
    label = (
        parse_text_schedule(value.get("label"))
        if value.get("label") is not None
        else None
    )
    reveal = value.get("reveal_target", True)
    targets = []
    raw = value.get("targets")
    if isinstance(raw, list):
        for t in raw:
            parsed = parse_target(t)
            if parsed is not None:
                targets.append(parsed)
    return ConnectionPath(
        label=label,
        reveal_target=reveal if isinstance(reveal, bool) else True,
        targets=targets,
    )


def slot_to_dict(s: ConnectionSlot) -> dict[str, Any]:
    return {
        "direction": s.direction,
        "enabled": s.enabled,
        "paths": [path_to_dict(p) for p in s.paths],
    }


def parse_slot(direction: str, value: Any) -> ConnectionSlot:
    if direction not in DIRECTIONS:
        raise ValueError(f"无效方向：{direction}")
    if not isinstance(value, dict):
        return ConnectionSlot(direction=direction)
    enabled = value.get("enabled", False)
    paths = []
    raw = value.get("paths")
    if isinstance(raw, list):
        for p in raw:
            paths.append(parse_path(p))
    return ConnectionSlot(
        direction=direction,
        enabled=enabled if isinstance(enabled, bool) else False,
        paths=paths,
    )


def default_connections() -> dict[str, ConnectionSlot]:
    """新地块的默认连接：4 个方向槽位全部禁用。"""
    return {d: ConnectionSlot(direction=d, enabled=False, paths=[]) for d in DIRECTIONS}


def location_to_dict(loc: Location) -> dict[str, Any]:
    return {
        "map_id": loc.map_id,
        "row": loc.row,
        "col": loc.col,
        "name": loc.name,
        "description": loc.description.to_dict() if loc.description else None,
        "connections": {d: slot_to_dict(s) for d, s in loc.connections.items()},
    }


def parse_location(value: Any) -> Location:
    if not isinstance(value, dict):
        raise ValueError("地块数据必须是对象")
    map_id = value.get("map_id", "")
    row = value.get("row")
    col = value.get("col")
    if not _is_int(row) or not _is_int(col):
        raise ValueError("地块坐标必须是整数")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("地块名称不能为空")
    description = None
    if value.get("description") is not None:
        description = parse_text_schedule(value.get("description"))
    conns = default_connections()
    raw = value.get("connections")
    if isinstance(raw, dict):
        for d, v in raw.items():
            if d in DIRECTIONS:
                conns[d] = parse_slot(d, v)
    return Location(
        map_id=str(map_id) if isinstance(map_id, str) else "",
        row=row,
        col=col,
        name=name.strip(),
        description=description,
        connections=conns,
    )


def map_to_dict(m: WorldMap) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description.to_dict() if m.description else None,
        "timezone": m.timezone,
        "spawn_row": m.spawn_row,
        "spawn_col": m.spawn_col,
    }


def parse_map(value: Any) -> WorldMap:
    if not isinstance(value, dict):
        raise ValueError("地图数据必须是对象")
    id_ = value.get("id")
    name = value.get("name")
    if (
        not isinstance(id_, str)
        or not id_
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise ValueError("地图 id 与名称不能为空")
    description = None
    if value.get("description") is not None:
        description = parse_text_schedule(value.get("description"))
    tz = value.get("timezone")
    spawn_row = value.get("spawn_row", 0)
    spawn_col = value.get("spawn_col", 0)
    if not _is_int(spawn_row) or not _is_int(spawn_col):
        raise ValueError("出生点必须是整数坐标")
    return WorldMap(
        id=id_,
        name=name.strip(),
        description=description,
        timezone=str(tz) if isinstance(tz, str) and tz else None,
        spawn_row=spawn_row,
        spawn_col=spawn_col,
    )


# ---------- 玩家与场景视图（移动 / 展示用） ----------


@dataclass
class Player:
    """玩家化身：位置 = (map_id, row, col)。人类玩家仅内存；agent 持久化。"""

    player_id: str
    name: str
    map_id: str
    row: int
    col: int
    is_agent: bool = False
    last_active_ts: float = 0.0
    user_id: str | None = None

    def pos_key(self) -> tuple[str, int, int]:
        return (self.map_id, self.row, self.col)

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "map_id": self.map_id,
            "row": self.row,
            "col": self.col,
            "is_agent": self.is_agent,
        }


@dataclass
class ScenePath:
    """场景中可见的一条路径（槽内索引即移动句柄；隐藏目标 target_name 为 None）。"""

    direction: str
    path_index: int
    label: str
    reveal_target: bool
    target_name: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "path": self.path_index,
            "label": self.label,
            "reveal_target": self.reveal_target,
            "target_name": self.target_name,
        }


@dataclass
class SceneView:
    """玩家当前场景：所在地块 + 已解析描述 + 可用路径列表（死引用已剔除）。"""

    player_id: str
    map_id: str
    row: int
    col: int
    location: Location
    description: str
    paths: list[ScenePath] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "map_id": self.map_id,
            "row": self.row,
            "col": self.col,
            "location": location_to_dict(self.location),
            "description": self.description,
            "paths": [p.to_dict() for p in self.paths],
        }


# ---------- 模板（复制预设） ----------


@dataclass
class WorldTemplate:
    """地块模板：复制预设，非继承。data 为模板负载 dict。

    目标存储策略：**同图目标存方向相对偏移**（{dr, dc}，放置时按地块位置平移）；
    **跨图目标存绝对 map_id+坐标**（{map_id, row, col}）原样复制。
    """

    id: str
    name: str
    data: dict[str, Any]


def location_to_template_data(loc: Location) -> dict[str, Any]:
    """把地块捕获为模板负载。"""

    def target_data(t: Target) -> dict[str, Any]:
        if not t.map_id or t.map_id == loc.map_id:
            return {"dr": t.row - loc.row, "dc": t.col - loc.col, "weight": t.weight}
        return {"map_id": t.map_id, "row": t.row, "col": t.col, "weight": t.weight}

    def path_data(p: ConnectionPath) -> dict[str, Any]:
        d: dict[str, Any] = {"reveal_target": p.reveal_target}
        if p.label:
            d["label"] = p.label.to_dict()
        d["targets"] = [target_data(t) for t in p.targets]
        return d

    return {
        "name": loc.name,
        "description": loc.description.to_dict() if loc.description else None,
        "connections": {
            d: {"enabled": s.enabled, "paths": [path_data(p) for p in s.paths]}
            for d, s in loc.connections.items()
        },
    }


def parse_template_data(data: Any, *, map_id: str, row: int, col: int) -> Location:
    """把模板负载解析为放置在 (map_id, row, col) 的地块；非法条目容错丢弃。

    同图目标（{dr, dc}）按放置位置平移；跨图目标（{map_id, row, col}）原样复制。
    """
    if not isinstance(data, dict):
        raise ValueError("模板数据必须是对象")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("模板名称不能为空")
    description = None
    if data.get("description") is not None:
        description = parse_text_schedule(data.get("description"))
    conns = default_connections()
    raw = data.get("connections")
    if isinstance(raw, dict):
        for d in DIRECTIONS:
            slot = raw.get(d)
            if not isinstance(slot, dict):
                continue
            enabled = slot.get("enabled", False)
            paths = []
            for p in slot.get("paths") or []:
                paths.append(_parse_template_path(p, map_id, row, col))
            conns[d] = ConnectionSlot(
                direction=d,
                enabled=enabled if isinstance(enabled, bool) else False,
                paths=paths,
            )
    return Location(
        map_id=map_id,
        row=row,
        col=col,
        name=name.strip(),
        description=description,
        connections=conns,
    )


def _parse_template_path(p: Any, map_id: str, row: int, col: int) -> ConnectionPath:
    if not isinstance(p, dict):
        return ConnectionPath()
    label = parse_text_schedule(p.get("label")) if p.get("label") is not None else None
    reveal = p.get("reveal_target", True)
    targets = []
    for t in p.get("targets") or []:
        if not isinstance(t, dict):
            continue
        weight = _norm_weight(t.get("weight", 1.0))
        if "dr" in t or "dc" in t:
            dr, dc = t.get("dr"), t.get("dc")
            if not _is_int(dr) or not _is_int(dc):
                continue
            targets.append(Target(map_id="", row=row + dr, col=col + dc, weight=weight))
        else:
            parsed = parse_target(t)
            if parsed is not None:
                targets.append(parsed)
    return ConnectionPath(
        label=label,
        reveal_target=reveal if isinstance(reveal, bool) else True,
        targets=targets,
    )
