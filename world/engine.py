"""世界引擎：有向图世界的核心动作。

协议无关——LLM 工具、插件页 API、未来世界 HTTP API（v2）共用同一套动作。
全部变更在实例级 ``asyncio.Lock`` 内完成；读路径走内存快照、免锁。
人类玩家仅内存（超时清理丢弃），agent 位置持久化到 SQLite（跨对话连续）。
"""

from __future__ import annotations

import asyncio
import logging
import time

from .model import Exit, ExitView, Location, Player, SceneView
from .store import AGENT_START_LOCATION, WorldStore

logger = logging.getLogger("astrbot")

AGENT_PLAYER_ID = "agent"
HUMAN_IDLE_TIMEOUT_SECONDS = 15 * 60
CLEANUP_INTERVAL_SECONDS = 60


class WorldError(Exception):
    """世界动作的业务错误（非法出口、玩家不存在等），消息可直接展示给用户。"""


class WorldEngine:
    """有向图世界的唯一权威引擎（插件进程内）。"""

    def __init__(self, store: WorldStore) -> None:
        self.store = store
        self._lock = asyncio.Lock()
        self._players: dict[str, Player] = {}
        self._agent_player: Player | None = None
        self._cleanup_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """载入持久化数据并启动超时清理任务。"""
        await self.store.initialize()
        agent_location = self.store.agent_location_id or AGENT_START_LOCATION
        self._agent_player = Player(
            player_id=AGENT_PLAYER_ID,
            name="世界探索者（Agent）",
            location_id=agent_location,
            is_agent=True,
            last_active_ts=time.time(),
        )
        self._players[AGENT_PLAYER_ID] = self._agent_player
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def terminate(self) -> None:
        """取消清理任务并关闭存储连接。"""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        await self.store.close()

    # ---------- 只读接口（读内存快照，免锁） ----------

    def list_locations(self) -> list[Location]:
        return list(self.store.locations.values())

    def list_all_exits(self) -> list[Exit]:
        return list(self.store.exits.values())

    def list_exits(self, location_id: str) -> list[Exit]:
        return list(self.store.exits_by_from.get(location_id, []))

    def get_location(self, location_id: str) -> Location | None:
        return self.store.locations.get(location_id)

    def get_exit(self, exit_id: str) -> Exit | None:
        return self.store.exits.get(exit_id)

    def get_player(self, player_id: str) -> Player | None:
        return self._players.get(player_id)

    def list_players(self) -> list[Player]:
        return list(self._players.values())

    # ---------- 玩家生命周期 ----------

    async def register_player(
        self,
        player_id: str,
        name: str | None = None,
        *,
        is_agent: bool = False,
        user_id: str | None = None,
    ) -> Location:
        """注册玩家到起始地块（幂等：已存在则只刷新活跃时间）。

        Raises:
            WorldError: 世界尚未初始化（无任何地块）。
        """
        async with self._lock:
            if not self.store.locations:
                raise WorldError("世界尚未初始化")
            existing = self._players.get(player_id)
            if existing is not None:
                existing.last_active_ts = time.time()
                if name:
                    existing.name = name
                return self.store.locations[existing.location_id]
            start = self.store.locations.get(
                AGENT_START_LOCATION, next(iter(self.store.locations.values()))
            )
            player = Player(
                player_id=player_id,
                name=name or f"旅行者-{player_id[-4:].upper()}",
                location_id=start.id,
                is_agent=is_agent,
                last_active_ts=time.time(),
                user_id=user_id,
            )
            self._players[player_id] = player
            if is_agent:
                self._agent_player = player
            return start

    async def deregister_player(self, player_id: str) -> bool:
        """注销人类玩家（agent 不可注销）；页面 unload 尽力调用，超时清理兜底。"""
        async with self._lock:
            player = self._players.get(player_id)
            if player is None or player.is_agent:
                return False
            del self._players[player_id]
            return True

    async def touch(self, player_id: str) -> bool:
        """刷新玩家活跃时间（页面心跳）。"""
        async with self._lock:
            player = self._players.get(player_id)
            if player is None:
                return False
            player.last_active_ts = time.time()
            return True

    # ---------- 核心动作 ----------

    async def describe_scene(self, player_id: str) -> SceneView | None:
        """返回玩家当前场景；玩家不存在返回 None。"""
        player = self._players.get(player_id)
        if player is None:
            return None
        location = self.store.locations.get(player.location_id)
        if location is None:
            return None
        return self._build_scene(player)

    async def move(self, player_id: str, exit_id: str) -> SceneView:
        """按出口 id 移动玩家。

        多边同目标 / 隐藏目标下，出口 id 才是移动的唯一语义（不能按目标名移动）。

        Raises:
            WorldError: 玩家不存在 / 出口不存在 / 出口不属于当前地块。
        """
        async with self._lock:
            player = self._players.get(player_id)
            if player is None:
                raise WorldError(f"玩家不存在：{player_id}")
            exit_ = self.store.exits.get(exit_id)
            if exit_ is None:
                raise WorldError(f"出口不存在：{exit_id}")
            if exit_.from_id != player.location_id:
                raise WorldError(f"出口「{exit_.label}」不在当前地块")
            target = self.store.locations.get(exit_.to_id)
            if target is None:
                raise WorldError(f"出口「{exit_.label}」的目标地块不存在")
            player.location_id = target.id
            player.last_active_ts = time.time()
            if player.is_agent:
                await self.store.save_agent_location(target.id)
            return self._build_scene(player)

    def _build_scene(self, player: Player) -> SceneView:
        location = self.store.locations[player.location_id]
        exits: list[ExitView] = []
        for e in self.store.exits_by_from.get(player.location_id, []):
            target = self.store.locations.get(e.to_id)
            exits.append(
                ExitView(
                    exit_id=e.id,
                    label=e.label,
                    target_name=target.name if (e.reveal_target and target) else None,
                )
            )
        return SceneView(player_id=player.player_id, location=location, exits=exits)

    # ---------- 超时清理 ----------

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            try:
                removed = await self._cleanup_idle_players()
                if removed:
                    logger.info(f"[worlditor] 清理超时无活动玩家 {removed} 个")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("[worlditor] 玩家清理任务异常")

    async def _cleanup_idle_players(self) -> int:
        """移除活跃时间超过阈值的人类玩家（agent 永不清除）。"""
        now = time.time()
        async with self._lock:
            stale = [
                pid
                for pid, p in self._players.items()
                if not p.is_agent
                and now - p.last_active_ts > HUMAN_IDLE_TIMEOUT_SECONDS
            ]
            for pid in stale:
                del self._players[pid]
            return len(stale)


def scene_to_text(scene: SceneView) -> str:
    """把场景渲染为中文文本（LLM 工具注入下一轮 prompt 的形态）。"""
    lines = [
        f"你当前位于：{scene.location.name}（{scene.location.id}）",
        f"描述：{scene.location.description}",
    ]
    if scene.exits:
        lines.append("可移动的出口：")
        for e in scene.exits:
            target = e.target_name if e.target_name else "???"
            lines.append(f"  [{e.exit_id}] {e.label} → {target}")
    else:
        lines.append("这里没有任何出口，你似乎被困住了。")
    return "\n".join(lines)
