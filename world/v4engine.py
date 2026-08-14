"""v4 世界引擎：世界底子的核心动作（协议无关，见 DESIGN_V4.md）。

与 v3 WorldEngine 的差异：
- **实体统一模型**（B12）：玩家/agent/布景实体都是 ``Entity``（entities 表），
  v3 的"玩家"概念被身份化实体（kind=player/agent）取代。
- **物品原语**：give/take/count/list（定义与持有分离，持有关系内核保证）。
- **交互**：declarative effects 由内核结算（A1），handler 由玩法包注册。
- **事件总线**：单一事件源（9 事件），玩法包订阅 + world_log 历史；
  SSE 是 v4.1 的序列化出口。
- **注册表**：kind / interaction / event / ui 组件与钩子，玩法包扩展入口。
- **广播**（B2）：say(scope=world) 消耗内置喇叭 + 每人 30s 冷却（管理员豁免）。
- **on_tick 调度**（A3）：单循环按 1s 粒度检查，各 handler 各自间隔，
  串行执行 + 异常隔离。

并发模型：实例级**可重入**异步锁（AsyncRLock）——事件/tick handler 由引擎在
锁内调用，handler 内再调 API 原语必须重入（普通 asyncio.Lock 会自锁死锁）。
时钟与 PRNG 注入（``clock`` / ``rand``），保证时间感知描述与加权抽取可测。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .v3model import (
    DIRECTIONS,
    ConnectionPath,
    ScenePath,
    SceneView,
    Target,
    WorldMap,
    parse_location,
    parse_map,
    parse_path,
    parse_text_schedule,
)
from .v4model import (
    WORLD_EVENTS,
    Effect,
    Entity,
    EntityKindSpec,
    InteractionRequest,
    InteractionResult,
    ItemDef,
    MenuButton,
    check_count,
)
from .v4store import MEGAPHONE_ITEM_ID, V4WorldStore

logger = logging.getLogger("astrbot")

BROADCAST_COOLDOWN_SECONDS = 30.0
TICK_GRANULARITY_SECONDS = 1.0
IDENTITY_KINDS = ("player", "agent")

# 哨兵：update 类动作用于区分「参数未提供（不变）」与「显式传 None（清空/重置）」
_UNSET = object()

# 交互 handler 签名：async def handler(api, req: InteractionRequest) -> InteractionResult
InteractionHandler = Callable[[Any, InteractionRequest], Any]
# 事件 handler 签名随事件（见 v4model.WORLD_EVENTS 注释）
WorldEventHandler = Callable[..., Any]
# UI 钩子 provider：async def provider(api, block: UiBlock) -> list[UiBlock]
UiHookProvider = Callable[..., Any]


class WorldError(Exception):
    """世界动作的业务错误（非法方向、实体不存在等），消息可直接展示给用户。"""


class AsyncRLock:
    """可重入异步锁：同一任务可多次 acquire（计数释放）。

    引擎在锁内调用玩法包 handler，handler 内再调 API 原语（同样走锁）——
    普通 asyncio.Lock 在这种场景会自锁死锁，需要重入。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._count = 0

    async def acquire(self) -> None:
        task = asyncio.current_task()
        if self._owner is task:
            self._count += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._count = 1

    def release(self) -> None:
        if self._owner is not asyncio.current_task():
            raise RuntimeError("锁释放者不是持有者")
        self._count -= 1
        if self._count == 0:
            self._owner = None
            self._lock.release()

    async def __aenter__(self) -> AsyncRLock:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.release()


@dataclass
class _EventBinding:
    """一次事件订阅（play_id 用于取 API 与 namespace）。"""

    play_id: str
    handler: WorldEventHandler
    interval: float = 0.0  # on_tick 专用：各自间隔（A3）
    last_run: float = 0.0


@dataclass
class _InteractionBinding:
    play_id: str
    handler: InteractionHandler
    label: str = ""


@dataclass
class _UiHookBinding:
    """一次界面钩子注册（B9：before/after/replace）。"""

    play_id: str
    provider: UiHookProvider


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


class V4WorldEngine:
    """世界底子的唯一权威引擎（插件进程内；v4 事实模型 + 原语 + 注册表 + 事件总线）。"""

    def __init__(
        self,
        store: V4WorldStore,
        *,
        clock: Callable[[], datetime] | None = None,
        rand: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self._lock = AsyncRLock()
        # 时钟返回带时区的 datetime（默认本地时区）；rand 返回 [0,1)（默认 random）
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._rand = rand
        # 注册表（玩法包扩展点）
        self._kind_specs: dict[str, EntityKindSpec] = {}
        self._interactions: dict[str, _InteractionBinding] = {}
        self._event_bindings: dict[str, list[_EventBinding]] = {
            e: [] for e in WORLD_EVENTS
        }
        self._ui_components: dict[str, str] = {}
        self._ui_hooks: dict[tuple[str, str], list[_UiHookBinding]] = {}
        # 玩法包 API 实例（PlayLoader attach；handler 调用时按 play_id 取）
        self._play_apis: dict[str, Any] = {}
        # 广播冷却（B2，内存；重启重置可接受——限流本来就是临时性的）
        self._broadcast_cd: dict[str, float] = {}
        # 管理员实体（喇叭豁免，B2；v4.1 管理端点维护）
        self.admins: set[str] = set()
        # 事件流订阅者（SSE 出口，B11：事件总线序列化推送；队列满丢最旧）
        self._subscribers: set[asyncio.Queue] = set()
        self._tick_task: asyncio.Task | None = None

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        """载入持久化数据并启动 on_tick 心跳循环。"""
        await self.store.initialize()
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def terminate(self) -> None:
        """取消心跳循环并关闭存储连接。"""
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
        await self.store.close()

    # ---------- 辅助 ----------

    def _now_ts(self) -> float:
        return self._clock().timestamp()

    def _default_map_id(self) -> str:
        m = next(iter(self.store.maps.values()), None)
        if m is None:
            raise WorldError("世界尚未初始化")
        return m.id

    def _map_arg(self, map_id: object) -> str:
        if map_id in (None, ""):
            return self._default_map_id()
        if not isinstance(map_id, str) or map_id not in self.store.maps:
            raise WorldError(f"地图不存在：{map_id}")
        return map_id

    def _now_for(self, m: Any) -> datetime:
        now = self._clock()
        if m and m.timezone:
            try:
                return now.astimezone(ZoneInfo(m.timezone))
            except (KeyError, ValueError):
                return now
        return now

    def _require_entity(self, entity_id: str) -> Entity:
        entity = self.store.entities.get(entity_id)
        if entity is None:
            raise WorldError(f"实体不存在：{entity_id}")
        return entity

    def _check_count(self, value: object) -> int:
        try:
            return check_count(value)
        except ValueError as e:
            raise WorldError(str(e)) from None

    # ---------- 玩法包注册（WorlditorPlayAPI 转发至此） ----------

    def register_item_def(self, item: Any) -> None:
        """注册/更新物品定义（同步更新内存；``flush_item_defs`` 批量落库）。

        物品 id 是类型键（玩法包引用），注册冲突即覆盖更新（同 id 视为同一物品）。
        """
        if not isinstance(item, ItemDef) and not (
            hasattr(item, "id") and hasattr(item, "name") and hasattr(item, "to_dict")
        ):
            raise WorldError("物品定义格式错误")
        if not isinstance(item.id, str) or not item.id:
            raise WorldError("物品 id 不能为空")
        if not isinstance(item.name, str) or not item.name.strip():
            raise WorldError("物品名称不能为空")
        self.store.items[item.id] = item

    async def flush_item_defs(self) -> None:
        """把内存中的物品定义全量写回 items 表（PlayLoader 加载结束后调用）。"""
        async with self._lock:
            for item in list(self.store.items.values()):
                await self.store.save_item(item)

    def register_entity_kind(
        self,
        kind: str,
        *,
        block_move: bool = False,
        interactions: tuple[str, ...] = (),
        tick: bool = False,
        label: str = "",
        play_id: str = "",
    ) -> None:
        """注册实体 kind 元数据（B1 / B8 / C3）。kind 未注册也可放置实体。"""
        kind = _clean_required(kind, "kind")
        if not isinstance(block_move, bool) or not isinstance(tick, bool):
            raise WorldError("block_move/tick 必须是布尔值")
        if not isinstance(interactions, (tuple, list)) or not all(
            isinstance(a, str) and a for a in interactions
        ):
            raise WorldError("interactions 必须是动作名列表")
        self._kind_specs[kind] = EntityKindSpec(
            kind=kind,
            block_move=block_move,
            interactions=tuple(interactions),
            tick=tick,
            label=str(label or ""),
            play_id=play_id,
        )

    def register_interaction(
        self,
        action: str,
        handler: InteractionHandler,
        *,
        label: str = "",
        play_id: str = "",
    ) -> None:
        """注册全局交互动作（C3：可用动作 = kind 声明 ∪ 全局注册表）。"""
        action = _clean_required(action, "动作名")
        if not callable(handler):
            raise WorldError("交互 handler 必须是可调用对象")
        self._interactions[action] = _InteractionBinding(
            play_id=play_id, handler=handler, label=str(label or action)
        )

    def register_world_event(
        self,
        event: str,
        handler: WorldEventHandler,
        *,
        interval: float = 0.0,
        play_id: str = "",
    ) -> None:
        """订阅世界事件；on_tick 需给出 interval（各自间隔，A3）。"""
        if event not in self._event_bindings:
            raise WorldError(f"未知事件：{event}")
        if not callable(handler):
            raise WorldError("事件 handler 必须是可调用对象")
        if event == "on_tick":
            if interval <= 0:
                raise WorldError("on_tick 需要正的 interval 秒数")
        self._event_bindings[event].append(
            _EventBinding(play_id=play_id, handler=handler, interval=float(interval))
        )

    def register_ui_component(
        self, name: str, web_entry: str, *, play_id: str = ""
    ) -> None:
        """注册自定义界面组件（B9；v4.1 WebUI 落地）。"""
        name = _clean_required(name, "组件名")
        if play_id:
            name = f"{play_id}.{name}"
        self._ui_components[name] = _clean_required(web_entry, "组件入口")

    def register_ui_hook(
        self,
        block_kind: str,
        position: str,
        provider: UiHookProvider,
        *,
        play_id: str = "",
    ) -> None:
        """向已有界面块注入子块（B9：before/after/replace；v4.1 渲染落地）。"""
        if position not in ("before", "after", "replace"):
            raise WorldError("position 必须是 before/after/replace 之一")
        if not callable(provider):
            raise WorldError("钩子 provider 必须是可调用对象")
        key = (block_kind, position)
        self._ui_hooks.setdefault(key, []).append(_UiHookBinding(play_id, provider))

    def attach_play_api(self, play_id: str, api: Any) -> None:
        """绑定玩法包 API 实例（PlayLoader 调用；事件 handler 按 play_id 取）。"""
        self._play_apis[play_id] = api

    def detach_play_api(self, play_id: str) -> None:
        self._play_apis.pop(play_id, None)

    def clear_play_registrations(self, play_id: str) -> None:
        """清理某玩法包的全部注册（kind / interaction / event / ui），
        供加载失败回滚与卸载使用（C2：重载 = 引擎重建，此为兜底清理）。"""
        self._kind_specs = {
            k: v for k, v in self._kind_specs.items() if v.play_id != play_id
        }
        self._interactions = {
            k: v for k, v in self._interactions.items() if v.play_id != play_id
        }
        for event in self._event_bindings:
            self._event_bindings[event] = [
                b for b in self._event_bindings[event] if b.play_id != play_id
            ]
        self._ui_components = {
            k: v
            for k, v in self._ui_components.items()
            if not k.startswith(f"{play_id}.")
        }
        self._ui_hooks = {
            k: [b for b in v if b.play_id != play_id]
            for k, v in self._ui_hooks.items()
            if any(b.play_id != play_id for b in v)
        }

    # ---------- 界面扩展（B9：ui_hook before/after/replace 递归展开） ----------

    async def apply_ui_hooks(self, block: Any | None) -> Any | None:
        """把玩法包注册的界面钩子应用到界面块（递归展开子块）。

        before/after：注入子块；replace：整体替换目标块（provider 返回的
        首个子块）。provider 异常被隔离（记日志跳过），不破坏渲染。

        Args:
            block: UiBlock 或 None。

        Returns:
            展开后的 UiBlock；``block`` 为 None 时返回 None。
        """
        from .v4model import UiBlock

        if block is None:
            return None
        if not isinstance(block, UiBlock):
            return block
        # 1. 先递归展开子块
        block.blocks = await self._expand_children(block.blocks)
        # 2. 本块钩子
        before: list[UiBlock] = []
        after: list[UiBlock] = []
        replaced: UiBlock | None = None
        for position in ("before", "after", "replace"):
            for binding in self._ui_hooks.get((block.kind, position), []):
                api = self._play_apis.get(binding.play_id)
                try:
                    injected = await self._invoke(binding.provider, api, block)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[worlditor] ui_hook(%s/%s) 异常：%s",
                        block.kind,
                        position,
                        binding.play_id,
                    )
                    continue
                if not isinstance(injected, list):
                    continue
                if position == "replace" and injected:
                    replaced = injected[0]
                elif position == "before":
                    before.extend(injected)
                elif position == "after":
                    after.extend(injected)
        if replaced is not None:
            # replace：整体替换；新块的子块递归展开（自身 replace 钩子不再
            # 应用，防 A 的 replace 又返回 A 的循环）
            replaced.blocks = await self._expand_children(replaced.blocks)
            return replaced
        block.blocks = before + block.blocks + after
        return block

    async def _expand_children(self, blocks: list) -> list:
        """递归展开一组子块（None 剔除）。"""
        from .v4model import UiBlock

        expanded: list[UiBlock] = []
        for child in blocks:
            child = await self.apply_ui_hooks(child)
            if child is not None:
                expanded.append(child)
        return expanded

    # ---------- 事件总线（单一事件源） ----------

    async def _invoke(self, fn: Callable, *args: Any) -> Any:
        """调用玩法包 handler：兼容 async / 同步函数（同步返回值直接透传）。"""
        result = fn(*args)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _emit(self, event: str, *args: Any, log: bool = True) -> None:
        """分发事件给订阅者（串行 + 异常隔离），并写入世界日志。

        调用方持锁；handler 内调原语可重入（AsyncRLock）。
        """
        for binding in list(self._event_bindings.get(event, [])):
            api = self._play_apis.get(binding.play_id)
            try:
                await self._invoke(binding.handler, api, *args)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[worlditor] 事件 handler 异常：%s (%s)", event, binding.play_id
                )
        if log:
            await self._append_log(event, args)
        if event != "on_tick":
            self._push_to_subscribers(self._event_payload(event, args))

    async def _append_log(self, event: str, args: tuple) -> None:
        """事件写入 world_log（历史/回放数据源；on_tick 不写）。"""
        entity_id = None
        for arg in args:
            if isinstance(arg, Entity):
                entity_id = arg.id
                break
            if isinstance(arg, InteractionRequest):
                entity_id = arg.entity_id
                break
        data: dict[str, Any] = {}
        for arg in args:
            if isinstance(arg, Entity):
                data["entity"] = arg.to_dict()
            elif isinstance(arg, InteractionRequest):
                data["request"] = {
                    "entity_id": arg.entity_id,
                    "target_id": arg.target.id if arg.target else None,
                    "action": arg.action,
                    "args": arg.args,
                    "item_id": arg.item_id,
                }
            elif isinstance(arg, InteractionResult):
                data["result"] = arg.to_dict()
            elif isinstance(arg, tuple):
                data.setdefault("positions", []).append(list(arg))
            elif isinstance(arg, dict):
                data.setdefault("dicts", []).append(arg)
            elif arg is not None:
                data.setdefault("values", []).append(arg)
        await self.store.append_world_log(
            self._now_ts(), entity_id, event, {"event": event, **data}
        )

    # ---------- 事件流订阅（SSE 出口，B11） ----------

    def subscribe(self) -> asyncio.Queue:
        """订阅世界事件流：返回队列，事件入队（队列满丢最旧）。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _push_to_subscribers(self, payload: dict | None) -> None:
        if payload is None or not self._subscribers:
            return
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

    def _event_payload(self, event: str, args: tuple) -> dict | None:
        """事件 → SSE payload（WebUI 增量更新用；实体全量序列化）。"""
        entity = next((a for a in args if isinstance(a, Entity)), None)
        payload: dict[str, Any] = {
            "event": event,
            "ts": self._now_ts(),
            "entity": entity.to_dict() if entity else None,
        }
        if event == "on_say":
            payload["text"] = args[1]
            payload["scope"] = args[2]
        elif event == "on_entity_move":
            payload["from"] = list(args[1])
            payload["to"] = list(args[2])
        elif event == "on_entity_enter":
            payload["map_id"] = args[1]
            payload["row"] = args[2]
            payload["col"] = args[3]
        elif event == "on_interact":
            req, result = args[0], args[1]
            payload["request"] = {
                "action": req.action,
                "target_id": req.target.id if req.target else None,
                "item_id": req.item_id,
            }
            payload["result"] = result.to_dict()
        elif event == "on_item_used":
            payload["item_id"] = args[1]
            payload["count"] = args[2]
            payload["result"] = (
                args[4].to_dict() if isinstance(args[4], InteractionResult) else None
            )
        elif event == "on_entity_changed":
            payload["changed"] = args[1]
        elif event == "on_entity_removed":
            payload["entity"] = args[0].to_dict()
        elif event == "on_world_edited":
            payload["what"] = args[0]
        return payload

    # ---------- 心跳（A3：单循环 + 各自间隔 + 串行 + 异常隔离） ----------

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_GRANULARITY_SECONDS)
            try:
                await self._tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("[worlditor] 心跳循环异常")

    async def _tick_once(self) -> None:
        """心跳一轮：到期 on_tick handler 依次执行（各自间隔）。"""
        bindings = [
            b for b in self._event_bindings.get("on_tick", []) if b.interval > 0
        ]
        if not bindings:
            return
        now = self._now_ts()
        async with self._lock:
            for binding in bindings:
                if now - binding.last_run < binding.interval:
                    continue
                dt = now - binding.last_run if binding.last_run else binding.interval
                binding.last_run = now
                api = self._play_apis.get(binding.play_id)
                try:
                    await self._invoke(binding.handler, api, dt)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[worlditor] on_tick handler 异常：%s", binding.play_id
                    )

    # ---------- 只读接口（读内存快照，免锁） ----------

    def get_entity(self, entity_id: str) -> Entity | None:
        return self.store.entities.get(entity_id)

    def list_entities(
        self, map_id: str | None = None, row: int | None = None, col: int | None = None
    ) -> list[Entity]:
        entities = list(self.store.entities.values())
        if map_id is not None:
            entities = [e for e in entities if e.map_id == map_id]
        if row is not None:
            entities = [e for e in entities if e.row == row]
        if col is not None:
            entities = [e for e in entities if e.col == col]
        return entities

    def get_map(self, map_id: str) -> Any | None:
        return self.store.maps.get(map_id)

    def list_maps(self) -> list:
        return list(self.store.maps.values())

    def get_location(self, map_id: str, row: int, col: int) -> Any | None:
        return self.store.loc_by_pos.get((map_id, row, col))

    def list_locations(self) -> list:
        return list(self.store.loc_by_pos.values())

    # ---------- 物品原语 ----------

    async def give_item(
        self, entity_id: str, item_id: str, count: int = 1, attrs: dict | None = None
    ) -> int:
        """给实体物品；返回持有总数。

        Raises:
            WorldError: 实体/物品不存在、count 非法。
        """
        async with self._lock:
            entity = self._require_entity(entity_id)
            if item_id not in self.store.items:
                raise WorldError(f"物品不存在：{item_id}")
            count = self._check_count(count)
            key = (entity_id, item_id)
            current = self.store.inventories.get(key)
            new_count = (current.count if current else 0) + count
            merged = current.attrs if current and attrs is None else dict(attrs or {})
            await self.store.set_inventory(entity_id, item_id, new_count, merged)
            await self._emit(
                "on_entity_changed",
                entity,
                {"inventory": {"item_id": item_id, "delta": count, "count": new_count}},
            )
            return new_count

    async def take_item(self, entity_id: str, item_id: str, count: int = 1) -> bool:
        """扣减实体物品；数量不足返回 False（不部分扣减）。

        Raises:
            WorldError: 实体不存在、count 非法。
        """
        async with self._lock:
            entity = self._require_entity(entity_id)
            count = self._check_count(count)
            key = (entity_id, item_id)
            current = self.store.inventories.get(key)
            if current is None or current.count < count:
                return False
            new_count = current.count - count
            await self.store.set_inventory(entity_id, item_id, new_count, current.attrs)
            await self._emit(
                "on_entity_changed",
                entity,
                {
                    "inventory": {
                        "item_id": item_id,
                        "delta": -count,
                        "count": new_count,
                    }
                },
            )
            return True

    def count_item(self, entity_id: str, item_id: str) -> int:
        entry = self.store.inventories.get((entity_id, item_id))
        return entry.count if entry else 0

    def list_inventory(self, entity_id: str) -> list[dict]:
        """实体背包：{item_id, def, count, attrs}；物品定义已删则 def 为 None。"""
        out = []
        for key, entry in self.store.inventories.items():
            if key[0] != entity_id:
                continue
            item = self.store.items.get(entry.item_id)
            out.append(
                {
                    "item_id": entry.item_id,
                    "def": item.to_dict() if item else None,
                    "count": entry.count,
                    "attrs": entry.attrs,
                }
            )
        return out

    # ---------- 实体原语 ----------

    async def place_entity(
        self,
        kind: str,
        map_id: str,
        row: int,
        col: int,
        *,
        name: str | None = None,
        desc: str = "",
        attrs: dict | None = None,
        state: dict | None = None,
        user_id: str | None = None,
    ) -> Entity:
        """放置实体（地图编辑内容，admin；B8）。

        kind 未注册也可放置（宽松：行为缺失而已）；name 缺省取 kind label。
        实体 id 自动生成 uuid4 hex（B5）；``user_id`` 供身份注册绑定账户
        （B13，仅身份化实体使用）。

        Raises:
            WorldError: 目标地块不存在 / 参数非法。
        """
        async with self._lock:
            map_id = self._map_arg(map_id)
            _check_pos(row, col)
            if (map_id, row, col) not in self.store.loc_by_pos:
                raise WorldError(f"地块不存在：({row}, {col})")
            kind = _clean_required(kind, "kind")
            spec = self._kind_specs.get(kind)
            if name is None or not str(name).strip():
                name = (spec.label if spec and spec.label else kind) if spec else kind
            entity = Entity(
                id=uuid.uuid4().hex,
                map_id=map_id,
                row=row,
                col=col,
                kind=kind,
                name=str(name).strip(),
                desc=str(desc or ""),
                attrs=dict(attrs or {}),
                state=dict(state or {}),
                user_id=user_id,
                last_active_ts=self._now_ts(),
            )
            await self.store.save_entity(entity)
            await self._emit(
                "on_world_edited",
                {"op": "place_entity", "entity_id": entity.id, "kind": kind},
            )
            return entity

    async def remove_entity(self, entity_id: str) -> None:
        """移除实体（地图编辑，admin；B8）并级联清理其背包。

        Raises:
            WorldError: 实体不存在。
        """
        async with self._lock:
            entity = self._require_entity(entity_id)
            await self.store.delete_entity(entity_id)
            await self._emit("on_entity_removed", entity)
            await self._emit(
                "on_world_edited", {"op": "remove_entity", "entity_id": entity_id}
            )

    async def move(
        self, entity_id: str, direction: str, *, path: int | None = None
    ) -> SceneView:
        """身份化实体按路径移动（v3 语义：死引用剔除 + 加权抽目标）。

        目标地块存在阻挡实体（block_move）时拒绝移动。

        Raises:
            WorldError: 实体不存在/非身份化、方向非法、无路径、被阻挡。
        """
        async with self._lock:
            entity = self._require_entity(entity_id)
            if entity.kind not in IDENTITY_KINDS:
                raise WorldError("只有玩家/agent 实体可以按路径移动")
            direction = _check_direction(direction)
            loc = self.store.loc_by_pos.get(entity.pos_key())
            if loc is None:
                raise WorldError(f"实体不在任何地块：{entity_id}")
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
                path_obj = usable[0][1]
            else:
                if not _is_int(path):
                    raise WorldError("path 必须是整数索引")
                match = [p for i, p in usable if i == path]
                if not match:
                    raise WorldError(f"该方向的路径索引 {path} 不存在")
                path_obj = match[0]
            tgt = self._draw_target(path_obj, loc.map_id)
            blocker = self._blocker_at(tgt.map_id, tgt.row, tgt.col)
            if blocker is not None:
                raise WorldError(f"被「{blocker.name}」挡住了，无法通行")
            from_pos = entity.pos_key()
            entity.map_id, entity.row, entity.col = tgt.map_id, tgt.row, tgt.col
            entity.last_active_ts = self._now_ts()
            await self.store.save_entity(entity)
            await self._emit("on_entity_move", entity, from_pos, entity.pos_key())
            await self._emit(
                "on_entity_enter", entity, entity.map_id, entity.row, entity.col
            )
            return self._build_scene(entity)

    async def move_entity(
        self, entity_id: str, map_id: str, row: int, col: int
    ) -> None:
        """实体直接移动到坐标（玩法包行为驱动，B8；传送语义，不做阻挡检查）。"""
        async with self._lock:
            entity = self._require_entity(entity_id)
            map_id = self._map_arg(map_id)
            _check_pos(row, col)
            if (map_id, row, col) not in self.store.loc_by_pos:
                raise WorldError(f"地块不存在：({row}, {col})")
            if entity.pos_key() == (map_id, row, col):
                return
            from_pos = entity.pos_key()
            entity.map_id, entity.row, entity.col = map_id, row, col
            entity.last_active_ts = self._now_ts()
            await self.store.save_entity(entity)
            await self._emit("on_entity_move", entity, from_pos, entity.pos_key())
            await self._emit("on_entity_enter", entity, map_id, row, col)

    async def set_attrs(self, entity_id: str, patch: dict) -> None:
        """合并写实体 attrs（玩法数据；C1 装备/格子自管）。"""
        async with self._lock:
            entity = self._require_entity(entity_id)
            if not isinstance(patch, dict):
                raise WorldError("patch 必须是对象")
            if not patch:
                return
            entity.attrs = {**entity.attrs, **patch}
            await self.store.save_entity(entity)
            await self._emit("on_entity_changed", entity, {"attrs": patch})

    def get_attrs(self, entity_id: str) -> dict:
        return dict(self._require_entity(entity_id).attrs)

    async def set_state(self, entity_id: str, patch: dict) -> None:
        """合并写实体 state（门开/关、库存、血量等玩法包自管状态）。"""
        async with self._lock:
            entity = self._require_entity(entity_id)
            if not isinstance(patch, dict):
                raise WorldError("patch 必须是对象")
            if not patch:
                return
            entity.state = {**entity.state, **patch}
            await self.store.save_entity(entity)
            await self._emit("on_entity_changed", entity, {"state": patch})

    def get_state(self, entity_id: str) -> dict:
        return dict(self._require_entity(entity_id).state)

    # ---------- 移动辅助（v3 语义） ----------

    def _resolve_main(self, p: ConnectionPath, from_map_id: str) -> Target | None:
        if not p.targets:
            return None
        return self.store.resolve_target(p.targets[0], from_map_id)

    def _draw_target(self, p: ConnectionPath, from_map_id: str) -> Target:
        """路径内按权重抽目标（主目标 + 意外目标），全部不可达则报错。"""
        candidates = [
            r
            for t in p.targets
            if (r := self.store.resolve_target(t, from_map_id)) is not None
        ]
        if not candidates:
            raise WorldError("该路径的目标都不可达")
        total = sum(c.weight for c in candidates)
        r = self._rand() if self._rand is not None else random.random()
        acc = 0.0
        chosen = candidates[-1]
        for c in candidates:
            acc += c.weight
            if r * total <= acc:
                chosen = c
                break
        return chosen

    def _blocker_at(self, map_id: str, row: int, col: int) -> Entity | None:
        for e in self.store.entities.values():
            if e.pos_key() == (map_id, row, col) and self._is_blocking(e):
                return e
        return None

    def _is_blocking(self, e: Entity) -> bool:
        """阻挡判定：state 可动态覆盖 kind 声明（门开/关由玩法包写 state）。"""
        if "block_move" in e.state:
            return bool(e.state["block_move"])
        spec = self._kind_specs.get(e.kind)
        return bool(spec and spec.block_move)

    def _build_scene(self, entity: Entity) -> SceneView:
        loc = self.store.loc_by_pos.get(entity.pos_key())
        if loc is None:
            raise WorldError(f"实体不在任何地块：{entity.id}")
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
                    target = self.store.loc_by_pos.get(
                        (main.map_id, main.row, main.col)
                    )
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
            player_id=entity.id,
            map_id=loc.map_id,
            row=loc.row,
            col=loc.col,
            location=loc,
            description=description,
            paths=paths,
        )

    # ---------- 广播（B2） ----------

    async def say(self, entity_id: str, text: str, *, scope: str = "cell") -> None:
        """说话。scope=cell 不限制；scope=world 消耗 1 个喇叭 + 每人 30s 冷却
        （管理员豁免）。

        Raises:
            WorldError: 文本为空 / scope 非法 / 无喇叭 / 冷却中。
        """
        async with self._lock:
            entity = self._require_entity(entity_id)
            text = _clean_required(text, "说话内容")
            if scope not in ("cell", "world"):
                raise WorldError("scope 必须是 cell 或 world")
            if scope == "world":
                if entity_id not in self.admins:
                    if self.count_item(entity_id, MEGAPHONE_ITEM_ID) < 1:
                        raise WorldError("全图广播需要「喇叭」，你身上没有")
                    now = self._now_ts()
                    last = self._broadcast_cd.get(entity_id, 0.0)
                    if now - last < BROADCAST_COOLDOWN_SECONDS:
                        remain = int(BROADCAST_COOLDOWN_SECONDS - (now - last)) + 1
                        raise WorldError(f"广播冷却中，约 {remain} 秒后再试")
                    await self.take_item(entity_id, MEGAPHONE_ITEM_ID)
                    self._broadcast_cd[entity_id] = now
            await self._emit("on_say", entity, text, scope)

    # ---------- 交互（WebUI / MCP 的公共入口，A1） ----------

    async def interact(
        self,
        entity_id: str,
        target_id: str,
        action: str,
        args: dict | None = None,
        item_id: str | None = None,
    ) -> InteractionResult:
        """发起一次交互：校验可用动作（C3）→ 玩法包 handler → 内核结算 effects。

        交互 handler 异常会被隔离并转为可展示的 WorldError（不拖垮内核）。

        Raises:
            WorldError: 实体/目标不存在、动作不可用或未实现、handler 出错、
                effects 结算失败。
        """
        async with self._lock:
            entity = self._require_entity(entity_id)
            target = self._require_entity(target_id)
            action = _clean_required(action, "动作")
            if action not in self._interactions:
                spec = self._kind_specs.get(target.kind)
                declared = set(spec.interactions) if spec else set()
                if action not in declared:
                    raise WorldError(f"「{target.name}」没有「{action}」这个动作")
                raise WorldError(f"动作「{action}」尚未实现")
            binding = self._interactions[action]
            req = InteractionRequest(
                entity_id=entity_id,
                target=target,
                action=action,
                args=dict(args or {}),
                item_id=item_id,
            )
            api = self._play_apis.get(binding.play_id)
            try:
                result = await self._invoke(binding.handler, api, req)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[worlditor] 交互 handler 异常：%s (%s)", action, binding.play_id
                )
                raise WorldError("交互执行出错，请稍后再试") from None
            if not isinstance(result, InteractionResult):
                raise WorldError("交互返回结果格式错误")
            await self._apply_effects(entity_id, result.effects)
            await self._emit("on_interact", req, result)
            if item_id is not None:
                count = self.count_item(entity_id, item_id)
                await self._emit(
                    "on_item_used", entity, item_id, count, req.args, result
                )
            return result

    def available_actions(self, target_id: str) -> list[str]:
        """实体可用动作（C3：kind 声明 ∪ 全局注册表；未实现的声明剔除）。"""
        target = self.store.entities.get(target_id)
        if target is None:
            raise WorldError(f"实体不存在：{target_id}")
        spec = self._kind_specs.get(target.kind)
        declared = set(spec.interactions) if spec else set()
        return sorted(
            a for a in (declared | set(self._interactions)) if a in self._interactions
        )

    def list_actions(self, target_id: str) -> list[MenuButton]:
        """实体可用动作按钮（UI 菜单生成用）。"""
        return [
            MenuButton(label=self._interactions[a].label, action=a)
            for a in self.available_actions(target_id)
        ]

    async def _apply_effects(self, entity_id: str, effects: list) -> None:
        """内核结算交互 effects（按 op 执行原语，校验合法性；锁内重入）。"""
        for raw in effects:
            effect = raw if isinstance(raw, Effect) else Effect.from_dict(raw)
            if effect is None:
                raise WorldError("无效的交互效果")
            op = effect.op
            args = effect.args if isinstance(effect.args, dict) else {}
            if op == "give_item":
                item_id = args.get("item_id")
                if not isinstance(item_id, str) or not item_id:
                    raise WorldError("give_item 效果缺少 item_id")
                await self.give_item(
                    entity_id,
                    item_id,
                    count=args.get("count", 1),
                    attrs=args.get("attrs"),
                )
            elif op == "take_item":
                item_id = args.get("item_id")
                if not isinstance(item_id, str) or not item_id:
                    raise WorldError("take_item 效果缺少 item_id")
                ok = await self.take_item(
                    entity_id, item_id, count=args.get("count", 1)
                )
                if not ok:
                    raise WorldError(f"物品不足：{item_id}")
            elif op == "move":
                direction = args.get("direction")
                if not isinstance(direction, str) or not direction:
                    raise WorldError("move 效果缺少 direction")
                await self.move(entity_id, direction, path=args.get("path"))
            elif op == "move_entity":
                row, col = args.get("row"), args.get("col")
                if not _is_int(row) or not _is_int(col):
                    raise WorldError("move_entity 效果需要 row/col 整数")
                await self.move_entity(entity_id, args.get("map_id", ""), row, col)
            elif op == "set_attrs":
                patch = args.get("patch")
                if not isinstance(patch, dict):
                    raise WorldError("set_attrs 效果需要 patch 对象")
                await self.set_attrs(entity_id, patch)
            elif op == "say":
                text = args.get("text")
                if not isinstance(text, str) or not text:
                    raise WorldError("say 效果缺少 text")
                await self.say(entity_id, text, scope=args.get("scope", "cell"))
            else:
                raise WorldError(f"未知交互效果：{op}")

    # ---------- 地图编辑（B8：地块/地图/实体编辑原语，admin 端点转发） ----------

    async def create_location(
        self,
        map_id: str,
        row: int,
        col: int,
        name: str,
        *,
        description: object = None,
    ) -> Any:
        """新建地块（重复坐标 / 空名称报错）。"""
        async with self._lock:
            map_id = self._map_arg(map_id)
            _check_pos(row, col)
            if (map_id, row, col) in self.store.loc_by_pos:
                raise WorldError(f"地块已存在：({row}, {col})")
            name = _clean_required(name, "地块名称")
            desc = parse_text_schedule(description) if description is not None else None
            loc = parse_location(
                {
                    "map_id": map_id,
                    "row": row,
                    "col": col,
                    "name": name,
                    "description": desc.to_dict() if desc else None,
                    "connections": {
                        d: {"direction": d, "enabled": False, "paths": []}
                        for d in DIRECTIONS
                    },
                }
            )
            await self.store.save_location(loc)
            await self._emit(
                "on_world_edited", {"op": "create_location", "pos": [map_id, row, col]}
            )
            return loc

    async def update_location(
        self,
        map_id: str,
        row: int,
        col: int,
        *,
        name: object = _UNSET,
        description: object = _UNSET,
    ) -> Any:
        """更新地块名称 / 描述（坐标只读；``description=None`` 显式清空）。"""
        async with self._lock:
            map_id = self._map_arg(map_id)
            _check_pos(row, col)
            loc = self.store.loc_by_pos.get((map_id, row, col))
            if loc is None:
                raise WorldError(f"地块不存在：({row}, {col})")
            if name is not _UNSET:
                loc.name = _clean_required(name, "地块名称")
            if description is not _UNSET:
                loc.description = (
                    None if description is None else parse_text_schedule(description)
                )
            await self.store.save_location(loc)
            await self._emit(
                "on_world_edited", {"op": "update_location", "pos": [map_id, row, col]}
            )
            return loc

    async def move_location(
        self, map_id: str, row: int, col: int, to_row: int, to_col: int
    ) -> Any:
        """移动地块：原子重写自身坐标 + 全图指向旧坐标的连接目标 + 实体位置。"""
        async with self._lock:
            map_id = self._map_arg(map_id)
            _check_pos(row, col)
            _check_pos(to_row, to_col)
            src = (map_id, row, col)
            dst = (map_id, to_row, to_col)
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
            # 3. 地块上的实体位置
            for e in self.store.entities.values():
                if e.pos_key() == src:
                    e.map_id, e.row, e.col = map_id, to_row, to_col
                    await self.store.save_entity(e)
            await self._emit(
                "on_world_edited",
                {"op": "move_location", "from": list(src), "to": list(dst)},
            )
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
    ) -> Any:
        """更新地块某方向槽位（``paths`` 整体替换，v3 结构）。"""
        async with self._lock:
            map_id = self._map_arg(map_id)
            _check_pos(row, col)
            loc = self.store.loc_by_pos.get((map_id, row, col))
            if loc is None:
                raise WorldError(f"地块不存在：({row}, {col})")
            direction = _check_direction(direction)
            slot = loc.connections[direction]
            if enabled is not _UNSET:
                if not isinstance(enabled, bool):
                    raise WorldError("enabled 必须是布尔值")
                slot.enabled = enabled
            if paths is not _UNSET:
                if not isinstance(paths, list):
                    raise WorldError("paths 必须是数组")
                slot.paths = [parse_path(p) for p in paths]
            await self.store.save_location(loc)
            await self._emit(
                "on_world_edited",
                {
                    "op": "update_connection",
                    "pos": [map_id, row, col],
                    "direction": direction,
                },
            )
            return loc

    async def update_entity(
        self,
        entity_id: str,
        *,
        name: object = _UNSET,
        desc: object = _UNSET,
        attrs: object = _UNSET,
        state: object = _UNSET,
    ) -> Entity:
        """更新实体字段（admin 编辑；attrs/state 整体替换）。"""
        async with self._lock:
            entity = self._require_entity(entity_id)
            if name is not _UNSET:
                entity.name = _clean_required(name, "名称")
            if desc is not _UNSET:
                entity.desc = str(desc or "")
            if attrs is not _UNSET:
                if not isinstance(attrs, dict):
                    raise WorldError("attrs 必须是对象")
                entity.attrs = dict(attrs)
            if state is not _UNSET:
                if not isinstance(state, dict):
                    raise WorldError("state 必须是对象")
                entity.state = dict(state)
            await self.store.save_entity(entity)
            await self._emit("on_entity_changed", entity, {"edited": True})
            return entity

    async def create_map(
        self,
        map_id: str,
        name: str,
        *,
        description: str | None = None,
        timezone: str | None = None,
        spawn_row: int = 0,
        spawn_col: int = 0,
    ) -> WorldMap:
        """新建地图（id 唯一）。"""
        async with self._lock:
            map_id = _clean_required(map_id, "地图 id")
            name = _clean_required(name, "地图名称")
            if map_id in self.store.maps:
                raise WorldError(f"地图已存在：{map_id}")
            _check_pos(spawn_row, spawn_col)
            m = parse_map(
                {
                    "id": map_id,
                    "name": name,
                    "description": parse_text_schedule(description).to_dict()
                    if description
                    else None,
                    "timezone": timezone,
                    "spawn_row": spawn_row,
                    "spawn_col": spawn_col,
                }
            )
            await self.store.save_map(m)
            await self._emit("on_world_edited", {"op": "create_map", "map_id": map_id})
            return m

    async def update_map(
        self,
        map_id: str,
        *,
        name: object = _UNSET,
        description: object = _UNSET,
        timezone: object = _UNSET,
        spawn_row: object = _UNSET,
        spawn_col: object = _UNSET,
    ) -> WorldMap:
        """更新地图属性（``timezone=None`` 显式清空为本地时区）。"""
        async with self._lock:
            m = self.store.maps.get(map_id)
            if m is None:
                raise WorldError(f"地图不存在：{map_id}")
            if name is not _UNSET:
                m.name = _clean_required(name, "地图名称")
            if description is not _UNSET:
                m.description = (
                    None if description is None else parse_text_schedule(description)
                )
            if timezone is not _UNSET:
                m.timezone = (
                    None
                    if timezone is None
                    else str(timezone)
                    if str(timezone).strip()
                    else None
                )
            if spawn_row is not _UNSET or spawn_col is not _UNSET:
                sr = m.spawn_row if spawn_row is _UNSET else spawn_row
                sc = m.spawn_col if spawn_col is _UNSET else spawn_col
                _check_pos(sr, sc)
                m.spawn_row, m.spawn_col = sr, sc
            await self.store.save_map(m)
            await self._emit("on_world_edited", {"op": "update_map", "map_id": map_id})
            return m

    # ---------- 地图编辑（B8：删除地块级联删除其上实体） ----------

    async def delete_location(self, map_id: str, row: int, col: int) -> None:
        """删除地块：级联清除全图指向它的连接目标 + 删除其上实体（B8）。

        拒绝删除有身份化实体占据的地块（玩家/agent 在场）。

        Raises:
            WorldError: 地块不存在 / 有身份化实体在场。
        """
        async with self._lock:
            map_id = self._map_arg(map_id)
            _check_pos(row, col)
            key = (map_id, row, col)
            if key not in self.store.loc_by_pos:
                raise WorldError(f"地块不存在：({row}, {col})")
            for e in self.store.entities.values():
                if e.pos_key() == key and e.kind in IDENTITY_KINDS:
                    raise WorldError(f"有玩家「{e.name}」位于该地块，无法删除")
            await self._clear_targets_to(key)
            await self.store.delete_location(map_id, row, col)
            for e in list(self.store.entities.values()):
                if e.pos_key() == key:
                    await self.store.delete_entity(e.id)
                    await self._emit("on_entity_removed", e)
            await self._emit(
                "on_world_edited", {"op": "delete_location", "pos": list(key)}
            )

    async def _clear_targets_to(self, key: tuple[str, int, int]) -> None:
        """重写全图：删除指向 ``key`` 的目标（主目标 → 整条路径移除，v3 语义）。"""
        for loc in list(self.store.loc_by_pos.values()):
            changed = False
            for slot in loc.connections.values():
                new_paths: list[ConnectionPath] = []
                for p in slot.paths:
                    main = self._resolve_main(p, loc.map_id)
                    if main is not None and (main.map_id, main.row, main.col) == key:
                        changed = True
                        continue  # 主目标被删 → 整条路径移除
                    kept = [
                        t for t in p.targets if self._target_key(t, loc.map_id) != key
                    ]
                    if len(kept) != len(p.targets):
                        changed = True
                    new_paths.append(
                        ConnectionPath(
                            label=p.label, reveal_target=p.reveal_target, targets=kept
                        )
                    )
                slot.paths = new_paths
            if changed:
                await self.store.save_location(loc)

    def _target_key(self, t: Target, from_map_id: str) -> tuple[str, int, int]:
        return (t.map_id or from_map_id, t.row, t.col)

    # ---------- 玩法数据 KV（play_data 表） ----------

    def kv_get(self, namespace: str, key: str, default: Any = None) -> Any:
        return self.store.play_data.get((namespace, key), default)

    async def kv_set(self, namespace: str, key: str, value: Any) -> None:
        async with self._lock:
            await self.store.set_play_kv(namespace, key, value)
