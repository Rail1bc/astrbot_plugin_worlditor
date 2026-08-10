"""Web API handler 包（插件页数据源，挂在 Star 上由 main.py 装配）。"""

from .play import PlayAPI
from .routes import _ROUTES
from .state import StateAPI

__all__ = ["_ROUTES", "PlayAPI", "StateAPI"]
