"""插件公共 API 包。

- v3：Web API handler（插件页数据源，挂在 Star 上由 main.py 装配）。
- v4：玩法包公共入口——`WorlditorPlayAPI` 与交互协议类型（玩法包
  `from astrbot_plugin_worlditor.api import ...`）。
- v4.1：身份 / 快照 / SSE / admin 管理端点（REST 非动作，B10）。
"""

from ..world.play.api import WorlditorPlayAPI
from ..world.v4engine import WorldError
from ..world.v4model import (
    Effect,
    Entity,
    InteractionRequest,
    InteractionResult,
    ItemDef,
    MenuButton,
    UiBlock,
)
from .admin import V4AdminAPI
from .auth_routes import V4AuthAPI
from .edit import EditAPI
from .play import PlayAPI
from .routes import _ROUTES, _V4_ROUTES
from .snapshot import V4SnapshotAPI
from .sse import V4SseAPI
from .state import StateAPI
from .static import V4StaticAPI

__all__ = [
    "_ROUTES",
    "_V4_ROUTES",
    "EditAPI",
    "PlayAPI",
    "StateAPI",
    "V4AdminAPI",
    "V4AuthAPI",
    "V4SnapshotAPI",
    "V4SseAPI",
    "V4StaticAPI",
    "WorlditorPlayAPI",
    "WorldError",
    "Effect",
    "Entity",
    "InteractionRequest",
    "InteractionResult",
    "ItemDef",
    "MenuButton",
    "UiBlock",
]
