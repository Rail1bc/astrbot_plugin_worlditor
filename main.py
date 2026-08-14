"""世界编辑器插件（astrbot_plugin_worlditor）。

世界以 (map_id, 行, 列) 为身份的地块组成，连接内嵌于地块的固定 4 方向槽位
（每槽多条平行路径，路径内首个目标 = 主目标、其余 = 意外加权目标）；支持
分时段加权描述（TextSchedule）、隐藏目标（reveal_target=false → ???）、
环路与非相邻连接，可实现"迷路"效果。

- 任意人类用户以隐形实体（随机 id、仅内存、超时清理）在地图上移动；
- agent 提供 ``world_look`` / ``world_move`` 两个工具，位置跨对话持久化（SQLite）；
- 框架内置插件网页（pages/world/）作为调试 / 编辑 / 游玩工具。

v4（DESIGN_V4.md）：世界底子内核与 v3 共存（同一 world.db，表互不干扰）——
v4 引擎（实体统一模型/物品/交互/事件总线/广播）在插件初始化时装配，并加载
玩法包（demo_play + 数据目录 plays/）。

v4.1：身份注册（auth_mode 三模式 + token 三档）、REST 非动作端点
（只读快照 / SSE / admin 管理 / 玩法包 web 资源）、进程内 MCP server
（HTTP 独立服务按配置启动；stdio 由独立进程入口）。

架构：world/ 世界引擎（协议无关，可被未来 MCP / 世界 HTTP API 复用），
api/ Web API handler（插件页 + v4 REST），本模块只做装配与 LLM 工具注册。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools

from .api import (
    _ROUTES,
    _V4_ROUTES,
    EditAPI,
    PlayAPI,
    StateAPI,
    V4AdminAPI,
    V4AuthAPI,
    V4SnapshotAPI,
    V4SseAPI,
    V4StaticAPI,
)
from .world.engine import AGENT_PLAYER_ID, WorldEngine, WorldError, scene_to_text
from .world.identity import IdentityService
from .world.mcp import build_mcp_server
from .world.mcp.http import WorldHttpServer, build_http_app
from .world.play import PlayLoader
from .world.store import WorldStore
from .world.v4engine import V4WorldEngine
from .world.v4store import V4WorldStore

PLUGIN_NAME = "astrbot_plugin_worlditor"
__version__ = "4.1.0"


class WorlditorPlugin(
    Star,
    StateAPI,
    PlayAPI,
    EditAPI,
    V4AuthAPI,
    V4SnapshotAPI,
    V4SseAPI,
    V4AdminAPI,
    V4StaticAPI,
):
    """世界入口：装配 v3 + v4 引擎、身份服务、MCP server 与全部 Web API。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.config = dict(config or {})
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.engine = WorldEngine(WorldStore(data_dir / "world.db"))
        # v4 底子内核（与 v3 共存：同一 world.db，v4 表互不干扰）
        self.v4_engine = V4WorldEngine(V4WorldStore(data_dir / "world.db"))
        self.identity = IdentityService(
            self.v4_engine,
            auth_mode=str(self.config.get("auth_mode", "open")),
            admin_key=str(self.config.get("admin_key", "") or ""),
            allow_agent_register=bool(self.config.get("allow_agent_register", True)),
        )
        self.play_loader = PlayLoader(
            self.v4_engine,
            plays_dir=data_dir / "plays",
            demo_dir=Path(__file__).resolve().parent / "demo_play",
            worlditor_version=__version__,
        )
        self.mcp_server = None
        self._http_server: WorldHttpServer | None = None
        self._http_task: asyncio.Task | None = None
        for path, handler, methods, desc in _ROUTES:
            context.register_web_api(
                f"/{PLUGIN_NAME}{path}", getattr(self, handler), methods, desc
            )
        for path, handler, methods, desc in _V4_ROUTES:
            context.register_web_api(
                f"/{PLUGIN_NAME}{path}", getattr(self, handler), methods, desc
            )

    async def initialize(self) -> None:
        """插件激活：载入持久化数据、加载玩法包、按配置启动世界 HTTP API。"""
        await self.engine.initialize()
        await self.v4_engine.initialize()
        plays = await self.play_loader.load_all(self.context)
        # MCP server（HTTP 传输按配置启动独立服务；stdio 由独立进程入口）
        self.mcp_server = build_mcp_server(self.v4_engine)
        if bool(self.config.get("enable_world_api", False)):
            try:
                port = int(self.config.get("world_api_port", 6288))
            except (TypeError, ValueError):
                port = 6288
            host = str(self.config.get("world_api_host", "0.0.0.0"))
            origins_raw = self.config.get("allowed_origins", "")
            allowed_origins = (
                [o.strip() for o in str(origins_raw).split(",") if o.strip()]
                if origins_raw
                else None
            )
            app = build_http_app(
                self.mcp_server,
                self.identity,
                engine=self.v4_engine,
                loader=self.play_loader,
                allowed_origins=allowed_origins,
                static_dir=Path(__file__).resolve().parent / "webui" / "dist",
            )
            self._http_server = WorldHttpServer(app, host=host, port=port)
            self._http_task = asyncio.create_task(self._http_server.start())
            logger.info(
                f"[worlditor] 世界 HTTP API 已启动：{host}:{port}"
                f"（MCP streamable HTTP + WebUI 数据端点 + 内置 WebUI）"
            )
        logger.info(
            f"[worlditor] 世界编辑器已就绪：地图 {len(self.engine.list_maps())} 张，"
            f"地块 {len(self.engine.list_locations())} 个，"
            f"模板 {len(self.engine.list_templates())} 个；"
            f"v4 实体 {len(self.v4_engine.list_entities())} 个，"
            f"玩法包 {len(plays)} 个。"
        )

    async def terminate(self) -> None:
        """插件停用 / 重载：停 HTTP 服务、卸载玩法包、关闭引擎。"""
        if self._http_server is not None:
            self._http_server.stop()
        if self._http_task is not None:
            try:
                await asyncio.wait_for(self._http_task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._http_task = None
        await self.play_loader.unload_all()
        await self.v4_engine.terminate()
        await self.engine.terminate()

    # ---------- LLM 工具（agent 化身，player_id 固定为 "agent"） ----------

    @llm_tool(name="world_look")
    async def tool_world_look(self, event: AstrMessageEvent) -> str:
        """查看你在世界中的当前位置与可移动的方向。

        每个方向可能有多条路径，以 [方向:路径索引] 列出；移动时使用 world_move
        并传入方向（多路径时带路径索引）。目标显示为 ??? 的路径意味着你看不清它
        通向哪里。
        """
        scene = await self.engine.describe_scene(AGENT_PLAYER_ID)
        if scene is None:
            return "世界尚未就绪，请稍后再试。"
        return scene_to_text(scene)

    @llm_tool(name="world_move")
    async def tool_world_move(
        self, event: AstrMessageEvent, direction: str, path: int | None = None
    ) -> str:
        """沿方向移动到新位置，并返回新位置的场景。

        Args:
            direction(string): 移动方向，必须是 world_look 返回的 [方向] 之一
                （up/right/down/left）。
            path(number): 可选。该方向有多条平行路径时的路径索引，必须是
                world_look 返回列表中的一项；单条路径时可省略。
        """
        try:
            scene = await self.engine.move(AGENT_PLAYER_ID, direction, path=path)
        except WorldError as e:
            return f"移动失败：{e}"
        return scene_to_text(scene)
