"""世界引擎：世界编辑器的核心动作。

协议无关——LLM 工具、插件页 API、未来世界 HTTP API（v2）共用同一套动作。
全部变更在实例级 ``asyncio.Lock`` 内完成；读路径走内存快照、免锁。
人类玩家仅内存（超时清理丢弃），agent 位置持久化到 SQLite（跨对话连续）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from .model import DIRECTIONS, Exit, ExitView, Location, Player, SceneView
from .store import AGENT_START_LOCATION, WorldStore

logger = logging.getLogger("astrbot")

AGENT_PLAYER_ID = "agent"
HUMAN_IDLE_TIMEOUT_SECONDS = 15 * 60
CLEANUP_INTERVAL_SECONDS = 60

# 哨兵：update 类动作用于区分「参数未提供（不变）」与「显式传 None（清空/重置）」
_UNSET = object()


class WorldError(Exception):
    """世界动作的业务错误（非法出口、玩家不存在等），消息可直接展示给用户。"""


def _check_id(id_: object, what: str = "id") -> None:
    if not isinstance(id_, str) or not id_.strip():
        raise WorldError(f"{what}不能为空")


def _clean_required(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldError(f"{what}不能为空")
    return value.strip()


def _check_direction(direction: object) -> str:
    if direction not in DIRECTIONS:
        raise WorldError(f"方向必须是 {'/'.join(DIRECTIONS)} 之一")
    return str(direction)


def _check_bool(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise WorldError(f"{what}必须是布尔值")
    return value


def _coerce_layout(
    x: object, y: object, what: str = "布局坐标"
) -> tuple[float | None, float | None]:
    if x is None and y is None:
        return None, None
    if x is None or y is None:
        raise WorldError(f"{what}必须同时提供 x 与 y，或同时省略")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
    ):
        raise WorldError(f"{what}必须是数字")
    fx, fy = float(x), float(y)
    if not math.isfinite(fx) or not math.isfinite(fy):
        raise WorldError(f"{what}不能是 NaN 或无穷")
    return fx, fy


def _resolve_layout(
    loc: Location, x: object, y: object
) -> tuple[float | None, float | None]:
    """update 语义：双双省略=不变；双双 None=清空；双数字=更新。"""
    if x is _UNSET and y is _UNSET:
        return loc.layout_x, loc.layout_y
    if x is _UNSET or y is _UNSET:
        raise WorldError("布局坐标必须同时更新或同时省略")
    return _coerce_layout(x, y)


class WorldEngine:
    """世界编辑器的唯一权威引擎（插件进程内）。"""

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
                    direction=e.direction,
                )
            )
        return SceneView(player_id=player.player_id, location=location, exits=exits)

    # ---------- 地图编辑（协议无关；数据层不设出度限制） ----------

    async def create_location(
        self,
        id: str,
        name: str,
        description: str = "",
        *,
        layout_x: float | None = None,
        layout_y: float | None = None,
    ) -> Location:
        """新建地块；重复 id / 空 id 或名称抛错。"""
        async with self._lock:
            _check_id(id, "地块 id")
            name = _clean_required(name, "地块名称")
            description = (description or "").strip()
            layout_x, layout_y = _coerce_layout(layout_x, layout_y)
            if id in self.store.locations:
                raise WorldError(f"地块已存在：{id}")
            loc = Location(
                id=id,
                name=name,
                description=description,
                layout_x=layout_x,
                layout_y=layout_y,
            )
            await self.store.save_location(loc)
            return loc

    async def update_location(
        self,
        id: str,
        *,
        name: object = _UNSET,
        description: object = _UNSET,
        layout_x: object = _UNSET,
        layout_y: object = _UNSET,
    ) -> Location:
        """更新地块属性；缺省参数不变，layout 双双 None 表示清空坐标。"""
        async with self._lock:
            loc = self.store.locations.get(id)
            if loc is None:
                raise WorldError(f"地块不存在：{id}")
            name = loc.name if name is _UNSET else _clean_required(name, "地块名称")
            description = (
                loc.description
                if description is _UNSET
                else (description or "").strip()
            )
            x, y = _resolve_layout(loc, layout_x, layout_y)
            loc = Location(
                id=id,
                name=name,
                description=description,
                layout_x=x,
                layout_y=y,
            )
            await self.store.save_location(loc)
            return loc

    async def delete_location(self, id: str) -> None:
        """删除地块并级联删除所有以它为起点/终点的出边。

        拒绝删除仍被玩家（含 agent）占据的地块，保证 agent 位置与地图一致性。
        """
        async with self._lock:
            if id not in self.store.locations:
                raise WorldError(f"地块不存在：{id}")
            for p in self._players.values():
                if p.location_id == id:
                    raise WorldError(f"有玩家「{p.name}」位于该地块，无法删除")
            await self.store.delete_location_with_exits(id)

    async def create_exit(
        self,
        id: str,
        from_id: str,
        to_id: str,
        label: str,
        *,
        reveal_target: bool = True,
        direction: str = "up",
    ) -> Exit:
        """新建出口；from/to 必须存在，自环出口合法。"""
        async with self._lock:
            _check_id(id, "出口 id")
            label = _clean_required(label, "出口标签")
            reveal = _check_bool(reveal_target, "reveal_target")
            direction = _check_direction(direction)
            if id in self.store.exits:
                raise WorldError(f"出口已存在：{id}")
            if from_id not in self.store.locations:
                raise WorldError(f"出发地块不存在：{from_id}")
            if to_id not in self.store.locations:
                raise WorldError(f"目标地块不存在：{to_id}")
            exit_ = Exit(
                id=id,
                from_id=from_id,
                to_id=to_id,
                label=label,
                reveal_target=reveal,
                direction=direction,
            )
            await self.store.save_exit(exit_)
            return exit_

    async def update_exit(
        self,
        id: str,
        *,
        to_id: object = _UNSET,
        label: object = _UNSET,
        reveal_target: object = _UNSET,
        direction: object = _UNSET,
    ) -> Exit:
        """更新出口；缺省参数不变；from_id 不可变。"""
        async with self._lock:
            exit_ = self.store.exits.get(id)
            if exit_ is None:
                raise WorldError(f"出口不存在：{id}")
            to = exit_.to_id if to_id is _UNSET else to_id
            if to not in self.store.locations:
                raise WorldError(f"目标地块不存在：{to}")
            label = (
                exit_.label if label is _UNSET else _clean_required(label, "出口标签")
            )
            reveal = (
                exit_.reveal_target
                if reveal_target is _UNSET
                else _check_bool(reveal_target, "reveal_target")
            )
            direction = (
                exit_.direction if direction is _UNSET else _check_direction(direction)
            )
            exit_ = Exit(
                id=id,
                from_id=exit_.from_id,
                to_id=to,
                label=label,
                reveal_target=reveal,
                direction=direction,
            )
            await self.store.save_exit(exit_)
            return exit_

    async def delete_exit(self, id: str) -> None:
        """删除一条出口。"""
        async with self._lock:
            if id not in self.store.exits:
                raise WorldError(f"出口不存在：{id}")
            await self.store.delete_exit(id)

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
