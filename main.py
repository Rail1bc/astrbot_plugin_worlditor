"""有向图世界插件（astrbot_plugin_worlditor）。

世界是一个有向图：地块（Location）为节点，带标签的出口（Exit）为有向边。
`a→b` 可达**不蕴含** `b→a`；不存在空间相邻——只有出边才构成"相邻/可达"。
支持多边同目标、隐藏目标（reveal_target=false）与环路，可实现"迷路"效果。

v1 范围：基础地图 + 移动。
- 任意人类用户以隐形实体（随机 id、仅内存、超时清理）在地图上移动；
- agent 提供 ``world_look`` / ``world_move`` 两个工具，位置跨对话持久化（SQLite）；
- 框架内置插件网页（pages/world/）仅作调试/验证工具，非正式用户入口。

架构：world/ 世界引擎（协议无关，可被未来 MCP / 世界 HTTP API 复用），
api/ Web API handler（插件页），本模块只做装配与 LLM 工具注册。
"""

from __future__ import annotations

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools

from .api import _ROUTES, PlayAPI, StateAPI
from .world.engine import AGENT_PLAYER_ID, WorldEngine, WorldError, scene_to_text
from .world.store import WorldStore

PLUGIN_NAME = "astrbot_plugin_worlditor"


class WorlditorPlugin(Star, StateAPI, PlayAPI):
    """有向图世界的入口：装配引擎、注册 Web API 与 LLM 工具。"""

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
            f"[worlditor] 有向图世界已就绪：地块 {len(self.engine.list_locations())} 个，"
            f"出口 {len(self.engine.list_all_exits())} 条。"
        )

    async def terminate(self) -> None:
        """插件停用 / 重载：取消清理任务并关闭数据库。"""
        await self.engine.terminate()

    # ---------- LLM 工具（agent 化身，player_id 固定为 "agent"） ----------

    @llm_tool(name="world_look")
    async def tool_world_look(self, event: AstrMessageEvent) -> str:
        """查看你在世界中的当前位置与可移动的出口。

        出口以 [exit_id] 列出；移动时使用 world_move 并传入对应的 exit_id。
        目标显示为 ??? 的出口意味着你看不清它通向哪里。
        """
        scene = await self.engine.describe_scene(AGENT_PLAYER_ID)
        if scene is None:
            return "世界尚未就绪，请稍后再试。"
        return scene_to_text(scene)

    @llm_tool(name="world_move")
    async def tool_world_move(self, event: AstrMessageEvent, exit_id: str) -> str:
        """沿出口移动到新位置，并返回新位置的场景。

        Args:
            exit_id(string): 出口 id，必须是 world_look 返回的出口列表中的一项。
        """
        try:
            scene = await self.engine.move(AGENT_PLAYER_ID, exit_id)
        except WorldError as e:
            return f"移动失败：{e}"
        return scene_to_text(scene)
