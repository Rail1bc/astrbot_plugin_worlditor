"""世界编辑器的数据模型。

世界 = 有向图：地块（Location）是节点，带标签的出口（Exit）是有向边。
`a→b` 可达**不蕴含** `b→a`；不存在空间相邻——只有出边才构成"相邻/可达"。
同一 `(from_id, to_id)` 允许多条不同 label 的出边，`reveal_target=False` 的
出口在场景中隐藏目标名（显示 `???`），允许环路——共同实现"迷路"效果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DIRECTIONS = ("up", "right", "down", "left")


@dataclass
class Location:
    """一个地块（有向图节点）。

    ``layout_x`` / ``layout_y`` 仅为可视化提示，与拓扑无关（拓扑只由出边定义）。
    """

    id: str
    name: str
    description: str
    layout_x: float | None = None
    layout_y: float | None = None

    def as_dict(self) -> dict[str, Any]:
        layout: dict[str, float] | None
        if self.layout_x is not None and self.layout_y is not None:
            layout = {"x": self.layout_x, "y": self.layout_y}
        else:
            layout = None
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "layout": layout,
        }


@dataclass
class Exit:
    """一条带标签的有向出口（有向图边）。

    ``reveal_target=False`` 时场景中不暴露目标地块名（显示 `???`），是
    "迷路"效果的核心。``direction`` 为玩家视图十字布局的槽位方向
    （上/右/下/左），是结构化出口语义，与布局坐标无关；编辑器保证同一
    出发地块的出边方向互异（数据层不强制）。
    """

    id: str
    from_id: str
    to_id: str
    label: str
    reveal_target: bool = True
    direction: str = "up"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "label": self.label,
            "reveal_target": self.reveal_target,
            "direction": self.direction,
        }


@dataclass
class Player:
    """一个玩家化身。

    人类玩家（v1）为隐形实体：仅内存、随机 id、超时清理，不持久化；
    agent 化身固定 ``player_id="agent"``，位置持久化（SQLite）。
    ``user_id`` 为 v2 用户系统预留（注册用户 → 持久化玩家）。
    """

    player_id: str
    name: str
    location_id: str
    is_agent: bool = False
    last_active_ts: float = 0.0
    user_id: str | None = None


@dataclass
class ExitView:
    """场景中可见的一条出口（隐藏目标时 ``target_name`` 为 None）。"""

    exit_id: str
    label: str
    target_name: str | None
    direction: str = "up"


@dataclass
class SceneView:
    """玩家当前场景：所在地块 + 可见出口列表。"""

    player_id: str
    location: Location
    exits: list[ExitView] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "location": self.location.as_dict(),
            "exits": [
                {
                    "exit_id": e.exit_id,
                    "label": e.label,
                    "target_name": e.target_name,
                    "direction": e.direction,
                }
                for e in self.exits
            ],
        }


def parse_layout(layout_json: str | None) -> tuple[float | None, float | None]:
    """解析 ``layout_json``（如 ``{"x":100,"y":200}``）为坐标。

    坐标为可视化提示，与拓扑无关；非法/缺失返回 ``(None, None)``。
    """
    if not layout_json:
        return None, None
    try:
        data = json.loads(layout_json)
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    x, y = data.get("x"), data.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None, None
    return float(x), float(y)


def serialize_layout(x: float | None, y: float | None) -> str | None:
    """把坐标序列化为 ``layout_json`` 存储格式。"""
    if x is None or y is None:
        return None
    return json.dumps({"x": x, "y": y})
