"""世界编辑器插件（astrbot_plugin_worlditor）。

世界以 (map_id, 行, 列) 为身份的地块组成，连接内嵌于地块的固定 4 方向槽位
（每槽多条平行路径，路径内首个目标 = 主目标、其余 = 意外加权目标）；支持
分时段加权描述（TextSchedule）、隐藏目标（reveal_target=false → ???）、
环路与非相邻连接，可实现"迷路"效果。

- 任意人类用户以隐形实体（随机 id、仅内存、超时清理）在地图上移动；
- agent 提供 ``world_look`` / ``world_move`` 两个工具，位置跨对话持久化（SQLite）；
- 框架内置插件网页（pages/world/）作为调试 / 编辑 / 游玩工具。

架构：world/ 世界引擎（协议无关，可被未来 MCP / 世界 HTTP API 复用），
api/ Web API handler（插件页），本模块只做装配与 LLM 工具注册。
"""

from __future__ import annotations

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools

from .api import _ROUTES, EditAPI, PlayAPI, StateAPI
from .world.engine import AGENT_PLAYER_ID, WorldEngine, WorldError, scene_to_text
from .world.store import WorldStore

PLUGIN_NAME = "astrbot_plugin_worlditor"


class WorlditorPlugin(Star, StateAPI, PlayAPI, EditAPI):
    """世界编辑器的入口：装配引擎、注册 Web API 与 LLM 工具。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.engine = WorldEngine(WorldStore(data_dir / "world.db"))
        for path, handler, methods, desc in _ROUTES:
            context.register_web_api(
                f"/{PLUGIN_NAME}{path}", getattr(self, handler), methods, desc
            )

    async def initialize(self) -> None:
        """插件激活：载入持久化数据、恢复 agent 位置、启动清理任务。"""
        await self.engine.initialize()
        logger.info(
            f"[worlditor] 世界编辑器已就绪：地图 {len(self.engine.list_maps())} 张，"
            f"地块 {len(self.engine.list_locations())} 个，"
            f"模板 {len(self.engine.list_templates())} 个。"
        )

    async def terminate(self) -> None:
        """插件停用 / 重载：取消清理任务并关闭数据库。"""
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
