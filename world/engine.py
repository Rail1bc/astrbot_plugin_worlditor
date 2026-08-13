"""世界引擎：世界编辑器的核心动作。

协议无关——LLM 工具、插件页 API、未来世界 HTTP API 共用同一套动作。
全部变更在实例级 ``asyncio.Lock`` 内完成；读路径走内存快照、免锁。
人类玩家仅内存（超时清理丢弃），agent 位置持久化到 SQLite（跨对话连续）。

v3 模型要点（见 DESIGN.md）：
- 地块身份 = (map_id, 行, 列)；连接内嵌于地块的固定 4 方向槽位，每槽多条平行路径。
- 死引用：路径主目标不可解析（目标地图 / 地块不存在）→ 路径死（不展示 / 不可选）；
  意外目标不可解析 → 静默跳过；槽启用但全部路径死 → 视为禁用。
- 移动 = 按方向 + 路径索引，在路径内按权重抽目标（主目标 + 意外），跨图移动会切图。
- 时钟与 PRNG 注入（``clock`` / ``rand``），保证时间感知描述与加权抽取可测。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from .store import DEFAULT_MAP_ID, WorldStore
from .v3model import (
    DIRECTIONS,
    ConnectionPath,
    Location,
    Player,
    ScenePath,
    SceneView,
    Target,
    WorldMap,
    WorldTemplate,
    default_connections,
    location_to_template_data,
    parse_path,
    parse_template_data,
    parse_text_schedule,
)

logger = logging.getLogger("astrbot")

AGENT_PLAYER_ID = "agent"
HUMAN_IDLE_TIMEOUT_SECONDS = 15 * 60
CLEANUP_INTERVAL_SECONDS = 60

# 哨兵：update 类动作用于区分「参数未提供（不变）」与「显式传 None（清空/重置）」
_UNSET = object()


class WorldError(Exception):
    """世界动作的业务错误（非法方向、玩家不存在等），消息可直接展示给用户。"""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_pos(row: object, col: object) -> None:
    if not _is_int(row) or not _is_int(col):
        raise WorldError("地块坐标必须是整数")


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


class WorldEngine:
    """世界编辑器的唯一权威引擎（插件进程内）。"""

    def __init__(
        self,
        store: WorldStore,
        *,
        clock: Callable[[], datetime] | None = None,
        rand: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self._lock = asyncio.Lock()
        self._players: dict[str, Player] = {}
        self._agent_player: Player | None = None
        self._cleanup_task: asyncio.Task | None = None
        # 时钟返回带时区的 datetime（默认本地时区）；rand 返回 [0,1)（默认 random）
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._rand = rand

    async def initialize(self) -> None:
        """载入持久化数据并启动超时清理任务。"""
        await self.store.initialize()
        pos = self.store.agent_pos or self.default_spawn()
        self._agent_player = Player(
            player_id=AGENT_PLAYER_ID,
            name="世界探索者（Agent）",
            map_id=pos[0],
            row=pos[1],
            col=pos[2],
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

    # ---------- 辅助 ----------

    def _default_map(self) -> WorldMap | None:
        return self.store.maps.get(DEFAULT_MAP_ID) or next(
            iter(self.store.maps.values()), None
        )

    def default_spawn(self) -> tuple[str, int, int]:
        m = self._default_map()
        if m is None:
            raise WorldError("世界尚未初始化")
        return (m.id, m.spawn_row, m.spawn_col)

    def _map_arg(self, map_id: object) -> str:
        if map_id in (None, ""):
            m = self._default_map()
            if m is None:
                raise WorldError("世界尚未初始化")
            return m.id
        if not isinstance(map_id, str) or map_id not in self.store.maps:
            raise WorldError(f"地图不存在：{map_id}")
        return map_id

    def _now_for(self, m: WorldMap | None) -> datetime:
        now = self._clock()
        if m and m.timezone:
            try:
                return now.astimezone(ZoneInfo(m.timezone))
            except (KeyError, ValueError):
                return now
        return now

    def _loc_at(self, map_id: str, row: int, col: int) -> Location | None:
        return self.store.loc_by_pos.get((map_id, row, col))

    # ---------- 只读接口（读内存快照，免锁） ----------

    def list_maps(self) -> list[WorldMap]:
        return list(self.store.maps.values())

    def get_map(self, map_id: str) -> WorldMap | None:
        return self.store.maps.get(map_id)

    def list_locations(self) -> list[Location]:
        return list(self.store.loc_by_pos.values())

    def get_location(self, map_id: str, row: int, col: int) -> Location | None:
        return self._loc_at(map_id, row, col)

    def list_templates(self) -> list[WorldTemplate]:
        return list(self.store.templates.values())

    def get_template(self, template_id: str) -> WorldTemplate | None:
        return self.store.templates.get(template_id)

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
        """注册玩家到默认地图出生点（幂等：已存在则只刷新活跃时间）。

        Raises:
            WorldError: 世界尚未初始化（无任何地块）。
        """
        async with self._lock:
            m_id, s_row, s_col = self.default_spawn()
            start = self._loc_at(m_id, s_row, s_col)
            if start is None:
                raise WorldError("世界尚未初始化")
            existing = self._players.get(player_id)
            if existing is not None:
                existing.last_active_ts = time.time()
                if name:
                    existing.name = name
                return self._loc_at(*existing.pos_key()) or start
            player = Player(
                player_id=player_id,
                name=name or f"旅行者-{player_id[-4:].upper()}",
                map_id=m_id,
                row=s_row,
                col=s_col,
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
        loc = self._loc_at(*player.pos_key())
        if loc is None:
            return None
        return self._build_scene(player)

    def _build_scene(self, player: Player) -> SceneView:
        loc = self._loc_at(*player.pos_key())
        m = self.store.maps.get(loc.map_id)
        now = self._now_for(m)
        rand = self._rand
        description = loc.description.resolve(now, rand) if loc.description else ""
        paths: list[ScenePath] = []
        for d in DIRECTIONS:
            slot = loc.connections.get(d)
            if slot is None or not slot.enabled:
                continue
            for idx, p in enumerate(slot.paths):
                main = self._resolve_main(p, loc.map_id)
                if main is None:
                    continue  # 死引用：主目标不可解析 → 整条路径不展示
                label = p.label.resolve(now, rand) if p.label else ""
                target_name = None
                if p.reveal_target:
                    target = self._loc_at(main.map_id, main.row, main.col)
                    target_name = target.name if target else None
                paths.append(
                    ScenePath(
                        direction=d,
                        path_index=idx,
                        label=label,
                        reveal_target=p.reveal_target,
                        target_name=target_name,
                    )
                )
        return SceneView(
            player_id=player.player_id,
            map_id=loc.map_id,
            row=loc.row,
            col=loc.col,
            location=loc,
            description=description,
            paths=paths,
        )

    def _resolve_main(self, p: ConnectionPath, from_map_id: str) -> Target | None:
        if not p.targets:
            return None
        return self.store.resolve_target(p.targets[0], from_map_id)

    async def move(
        self,
        player_id: str,
        direction: str,
        *,
        path: int | None = None,
        target: dict | None = None,
    ) -> SceneView:
        """按方向移动玩家；多路径时 ``path`` 指定槽内路径索引，单路径可省略。

        路径目标抽取：未显式 ``target`` 时在该路径的目标列表内按权重抽取
        （主目标 + 意外目标）；显式 ``target`` 则直取该坐标（须为路径目标之一）。

        Raises:
            WorldError: 玩家不存在 / 方向非法 / 该方向无可用路径 / 目标不可解析。
        """
        async with self._lock:
            player = self._players.get(player_id)
            if player is None:
                raise WorldError(f"玩家不存在：{player_id}")
            direction = _check_direction(direction)
            loc = self._loc_at(*player.pos_key())
            slot = loc.connections.get(direction)
            usable = []
            if slot is not None and slot.enabled:
                usable = [
                    (i, p)
                    for i, p in enumerate(slot.paths)
                    if self._resolve_main(p, loc.map_id) is not None
                ]
            if not usable:
                raise WorldError("这个方向没有可走的路径")
            if path is None:
                if len(usable) > 1:
                    raise WorldError("该方向有多条路径，请指定 path 索引")
                p = usable[0][1]
            else:
                if not _is_int(path):
                    raise WorldError("path 必须是整数索引")
                match = [p for i, p in usable if i == path]
                if not match:
                    raise WorldError(f"该方向的路径索引 {path} 不存在")
                p = match[0]
            if target is not None:
                tgt = self._explicit_target(target, p, loc.map_id)
            else:
                candidates = [
                    r
                    for t in p.targets
                    if (r := self.store.resolve_target(t, loc.map_id)) is not None
                ]
                if not candidates:
                    raise WorldError("该路径的目标都不可达")
                total = sum(c.weight for c in candidates)
                r = random.random() if self._rand is None else self._rand()
                acc = 0.0
                tgt = candidates[-1]
                for c in candidates:
                    acc += c.weight
                    if r * total <= acc:
                        tgt = c
                        break
            player.map_id, player.row, player.col = tgt.map_id, tgt.row, tgt.col
            player.last_active_ts = time.time()
            if player.is_agent:
                await self.store.save_agent_pos(tgt.map_id, tgt.row, tgt.col)
            return self._build_scene(player)

    def _explicit_target(
        self, target: dict, p: ConnectionPath, from_map_id: str
    ) -> Target:
        if not isinstance(target, dict):
            raise WorldError("target 必须是坐标对象")
        row = target.get("row")
        col = target.get("col")
        if not _is_int(row) or not _is_int(col):
            raise WorldError("target 的 row/col 必须是整数")
        map_id = target.get("map_id", "")
        if not isinstance(map_id, str):
            raise WorldError("target 的 map_id 必须是字符串")
        resolved = self.store.resolve_target(
            Target(map_id=map_id, row=row, col=col), from_map_id
        )
        if resolved is None:
            raise WorldError("目标坐标不可达")
        listed = {(r.map_id or from_map_id, r.row, r.col) for r in p.targets}
        if (resolved.map_id, resolved.row, resolved.col) not in listed:
            raise WorldError("目标坐标不在该路径的目标列表中")
        return resolved

    # ---------- 地图编辑（协议无关） ----------

    async def create_location(
        self,
        map_id: str,
        row: int,
        col: int,
        name: str,
        *,
        description: object = None,
        template_id: str | None = None,
    ) -> Location:
        """新建地块；重复坐标 / 空名称报错。``template_id`` 给出时以模板为蓝本
        （模板的 name/description/连接全部复制，显式 ``name`` 覆盖模板名）。"""
        async with self._lock:
            m = self._map_arg(map_id)
            _check_pos(row, col)
            if (m, row, col) in self.store.loc_by_pos:
                raise WorldError(f"地块已存在：({row}, {col})")
            if template_id is not None:
                tpl = self.store.templates.get(template_id)
                if tpl is None:
                    raise WorldError(f"模板不存在：{template_id}")
                loc = parse_template_data(tpl.data, map_id=m, row=row, col=col)
                name = name.strip() if isinstance(name, str) else ""
                if name and name != loc.name:
                    loc = replace(loc, name=name)
            else:
                name = _clean_required(name, "地块名称")
                desc = (
                    parse_text_schedule(description)
                    if description is not None
                    else None
                )
                loc = Location(
                    map_id=m,
                    row=row,
                    col=col,
                    name=name,
                    description=desc,
                    connections=default_connections(),
                )
            await self.store.save_location(loc)
            return loc

    async def update_location(
        self,
        map_id: str,
        row: int,
        col: int,
        *,
        name: object = _UNSET,
        description: object = _UNSET,
    ) -> Location:
        """更新地块属性；坐标只读，缺省参数不变；``description=None`` 显式清空。"""
        async with self._lock:
            m = self._map_arg(map_id)
            loc = self._loc_at(m, row, col)
            if loc is None:
                raise WorldError(f"地块不存在：({row}, {col})")
            name = loc.name if name is _UNSET else _clean_required(name, "地块名称")
            if description is _UNSET:
                desc = loc.description
            elif description is None:
                desc = None
            else:
                desc = parse_text_schedule(description)
            loc = replace(loc, name=name, description=desc)
            await self.store.save_location(loc)
            return loc

    async def delete_location(self, map_id: str, row: int, col: int) -> None:
        """删除地块：级联清除全图指向它的连接目标 + 拒绝删除有玩家占据的地块。

        级联规则：某路径的**主目标（首个）**指向被删地块 → 整条路径移除（对应旧
        模型「出边消失」）；意外目标（非首个）指向被删地块 → 仅移除该目标。
        """
        async with self._lock:
            m = self._map_arg(map_id)
            _check_pos(row, col)
            key = (m, row, col)
            if key not in self.store.loc_by_pos:
                raise WorldError(f"地块不存在：({row}, {col})")
            for p in self._players.values():
                if p.pos_key() == key:
                    raise WorldError(f"有玩家「{p.name}」位于该地块，无法删除")
            await self._clear_targets_to(key)
            await self.store.delete_location(m, row, col)

    async def _clear_targets_to(self, key: tuple[str, int, int]) -> None:
        """重写全图：删除指向 ``key`` 的目标（主目标 → 整条路径移除）。"""
        for loc in list(self.store.loc_by_pos.values()):
            changed = False
            for slot in loc.connections.values():
                new_paths: list[ConnectionPath] = []
                for p in slot.paths:
                    main = self._resolve_main(p, loc.map_id)
                    if (
                        main is not None
                        and (
                            main.map_id,
                            main.row,
                            main.col,
                        )
                        == key
                    ):
                        changed = True
                        continue  # 主目标被删 → 整条路径移除
                    kept = [
                        t for t in p.targets if self._target_key(t, loc.map_id) != key
                    ]
                    if len(kept) != len(p.targets):
                        changed = True
                    new_paths.append(
                        ConnectionPath(
                            label=p.label,
                            reveal_target=p.reveal_target,
                            targets=kept,
                        )
                    )
                slot.paths = new_paths
            if changed:
                await self.store.save_location(loc)

    def _target_key(self, t: Target, from_map_id: str) -> tuple[str, int, int]:
        return (t.map_id or from_map_id, t.row, t.col)

    async def move_location(
        self,
        map_id: str,
        row: int,
        col: int,
        to_row: int,
        to_col: int,
    ) -> Location:
        """移动地块：原子重写自身坐标 + 全图指向旧坐标的连接目标 + 该地块上玩家位置。

        目标格被占 → 拒绝（不做交换）。坐标只读，移动走此专门工具。
        """
        async with self._lock:
            m = self._map_arg(map_id)
            _check_pos(row, col)
            _check_pos(to_row, to_col)
            src = (m, row, col)
            dst = (m, to_row, to_col)
            if src not in self.store.loc_by_pos:
                raise WorldError(f"地块不存在：({row}, {col})")
            if dst in self.store.loc_by_pos:
                raise WorldError(f"目标格 ({to_row}, {to_col}) 已被占用")
            # 1. 全图引用重写（含自身自环）：指向旧坐标 → 新坐标
            for other in list(self.store.loc_by_pos.values()):
                changed = False
                for slot in other.connections.values():
                    for p in slot.paths:
                        new_targets = []
                        for t in p.targets:
                            if self._target_key(t, other.map_id) == src:
                                new_targets.append(
                                    Target(
                                        map_id=t.map_id,
                                        row=to_row,
                                        col=to_col,
                                        weight=t.weight,
                                    )
                                )
                                changed = True
                            else:
                                new_targets.append(t)
                        p.targets = new_targets
                if changed:
                    await self.store.save_location(other)
            # 2. 自身坐标迁移
            loc = self.store.loc_by_pos.pop(src)
            loc = replace(loc, row=to_row, col=to_col)
            self.store.loc_by_pos[dst] = loc
            await self.store.save_location(loc)
            # 3. 该地块上的玩家位置
            for p in self._players.values():
                if p.pos_key() == src:
                    p.map_id, p.row, p.col = m, to_row, to_col
                    if p.is_agent:
                        await self.store.save_agent_pos(m, to_row, to_col)
            return loc

    async def update_connection(
        self,
        map_id: str,
        row: int,
        col: int,
        direction: str,
        *,
        enabled: object = _UNSET,
        paths: object = _UNSET,
    ) -> Location:
        """更新地块某方向槽位；方向不可改；``paths`` 整体替换（含 label/reveal_target/targets）。"""
        async with self._lock:
            m = self._map_arg(map_id)
            _check_pos(row, col)
            loc = self._loc_at(m, row, col)
            if loc is None:
                raise WorldError(f"地块不存在：({row}, {col})")
            direction = _check_direction(direction)
            slot = loc.connections[direction]
            if enabled is not _UNSET:
                slot.enabled = _check_bool(enabled, "enabled")
            if paths is not _UNSET:
                if not isinstance(paths, list):
                    raise WorldError("paths 必须是数组")
                slot.paths = [parse_path(p) for p in paths]
            await self.store.save_location(loc)
            return loc

    # ---------- 模板 ----------

    async def create_template(
        self, template_id: str, name: str, *, map_id: str = "", row: int, col: int
    ) -> WorldTemplate:
        """从源地块捕获模板（同图目标存相对偏移，跨图目标存绝对坐标）。"""
        async with self._lock:
            if not isinstance(template_id, str) or not template_id.strip():
                raise WorldError("模板 id 不能为空")
            name = _clean_required(name, "模板名称")
            if template_id in self.store.templates:
                raise WorldError(f"模板已存在：{template_id}")
            m = self._map_arg(map_id)
            _check_pos(row, col)
            loc = self._loc_at(m, row, col)
            if loc is None:
                raise WorldError(f"地块不存在：({row}, {col})")
            tpl = WorldTemplate(
                id=template_id,
                name=name,
                data=location_to_template_data(loc),
            )
            await self.store.save_template(tpl)
            return tpl

    async def update_template(
        self,
        template_id: str,
        *,
        name: object = _UNSET,
        map_id: object = _UNSET,
        row: object = _UNSET,
        col: object = _UNSET,
    ) -> WorldTemplate:
        """更新模板：改名，或（同时提供 row/col）从新源地块重新捕获。"""
        async with self._lock:
            tpl = self.store.templates.get(template_id)
            if tpl is None:
                raise WorldError(f"模板不存在：{template_id}")
            if name is not _UNSET:
                tpl.name = _clean_required(name, "模板名称")
            if map_id is not _UNSET or row is not _UNSET or col is not _UNSET:
                if row is _UNSET or col is _UNSET:
                    raise WorldError("重新捕获需要同时提供 row 与 col")
                m = self._map_arg(map_id if map_id is not _UNSET else "")
                _check_pos(row, col)
                loc = self._loc_at(m, row, col)
                if loc is None:
                    raise WorldError(f"地块不存在：({row}, {col})")
                tpl.data = location_to_template_data(loc)
            await self.store.save_template(tpl)
            return tpl

    async def delete_template(self, template_id: str) -> None:
        """删除模板。"""
        async with self._lock:
            if template_id not in self.store.templates:
                raise WorldError(f"模板不存在：{template_id}")
            await self.store.delete_template(template_id)

    async def apply_template(
        self, template_id: str, *, map_id: str = "", row: int, col: int
    ) -> Location:
        """应用模板到空地块：同图目标按放置位置平移，跨图目标原样复制。"""
        async with self._lock:
            _check_pos(row, col)
            m = self._map_arg(map_id)
            if (m, row, col) in self.store.loc_by_pos:
                raise WorldError(f"目标格 ({row}, {col}) 已被占用")
            tpl = self.store.templates.get(template_id)
            if tpl is None:
                raise WorldError(f"模板不存在：{template_id}")
            loc = parse_template_data(tpl.data, map_id=m, row=row, col=col)
            await self.store.save_location(loc)
            return loc

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
        f"你当前位于：{scene.location.name}",
        f"描述：{scene.description}",
    ]
    if scene.paths:
        lines.append("可移动的方向：")
        for p in scene.paths:
            target = p.target_name if p.target_name else "???"
            if p.label:
                lines.append(f"  {p.direction}[{p.path_index}] {p.label} → {target}")
            else:
                lines.append(f"  {p.direction}[{p.path_index}] → {target}")
    else:
        lines.append("这里没有任何可走的路，你似乎被困住了。")
    return "\n".join(lines)
