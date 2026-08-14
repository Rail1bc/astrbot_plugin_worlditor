"""WorlditorPlayAPI：玩法包唯一入口（DESIGN_V4.md「WorlditorPlayAPI」）。

每个玩法包一个独立实例：kv 带 play id（namespace 隔离）；所有引擎动作
转发到 V4WorldEngine（引擎锁内执行）。玩法包拿不到引擎内部对象，只能通过
API 原语操作；API 版本随内核版本绑定。
"""

from __future__ import annotations

from typing import Any

from ..v4engine import V4WorldEngine
from ..v4model import ItemDef


class WorlditorPlayAPI:
    """玩法包 API（构造由 PlayLoader 完成；玩法包在 setup(api, context) 中使用）。"""

    def __init__(self, engine: V4WorldEngine, play_id: str) -> None:
        self._engine = engine
        self.play_id = play_id

    # ---------- 注册 ----------

    def register_item_def(self, item: ItemDef) -> None:
        """注册物品定义（持久化；同 id 覆盖更新）。"""
        self._engine.register_item_def(item)

    def register_entity_kind(
        self,
        kind: str,
        *,
        block_move: bool = False,
        interactions: tuple[str, ...] = (),
        tick: bool = False,
        label: str | None = None,
    ) -> None:
        """注册实体 kind 元数据（label 为 kind 标签文案，B1）。"""
        self._engine.register_entity_kind(
            kind,
            block_move=block_move,
            interactions=interactions,
            tick=tick,
            label=label or "",
            play_id=self.play_id,
        )

    def register_interaction(
        self, action: str, handler, *, label: str | None = None
    ) -> None:
        """注册全局交互动作（C3）；handler 签名 async (api, req) -> InteractionResult。"""
        self._engine.register_interaction(
            action, handler, label=label or "", play_id=self.play_id
        )

    def register_world_event(
        self, event: str, handler, *, interval: float = 0.0
    ) -> None:
        """订阅世界事件；on_tick 需给 interval（各自间隔秒数，A3）。"""
        self._engine.register_world_event(
            event, handler, interval=interval, play_id=self.play_id
        )

    def register_ui_component(self, name: str, web_entry: str) -> None:
        """注册自定义界面组件（B9；v4.1 WebUI 落地）。"""
        self._engine.register_ui_component(name, web_entry, play_id=self.play_id)

    def register_ui_hook(self, block_kind: str, position: str, provider) -> None:
        """向已有界面块注入子块（B9：before/after/replace；v4.1 渲染落地）。"""
        self._engine.register_ui_hook(
            block_kind, position, provider, play_id=self.play_id
        )

    # ---------- 只读 ----------

    def get_entity(self, entity_id: str):
        return self._engine.get_entity(entity_id)

    def list_entities(self, map_id=None, row=None, col=None) -> list:
        return self._engine.list_entities(map_id, row, col)

    def get_location(self, map_id: str, row: int, col: int):
        return self._engine.get_location(map_id, row, col)

    def get_map(self, map_id: str):
        return self._engine.get_map(map_id)

    def list_actions(self, target_id: str) -> list:
        """目标实体可用动作按钮（C3，UI 菜单生成用）。"""
        return self._engine.list_actions(target_id)

    # ---------- 玩法数据 KV（play_data 表，namespace 自动 = 玩法包 id） ----------

    def kv_get(self, key: str, default=None) -> Any:
        return self._engine.kv_get(self.play_id, key, default)

    async def kv_set(self, key: str, value: Any) -> None:
        await self._engine.kv_set(self.play_id, key, value)

    # ---------- 引擎动作（走原语，锁内执行；均按 entity_id） ----------

    async def give_item(
        self, entity_id: str, item_id: str, count: int = 1, attrs: dict | None = None
    ) -> int:
        return await self._engine.give_item(entity_id, item_id, count, attrs)

    async def take_item(self, entity_id: str, item_id: str, count: int = 1) -> bool:
        return await self._engine.take_item(entity_id, item_id, count)

    def count_item(self, entity_id: str, item_id: str) -> int:
        return self._engine.count_item(entity_id, item_id)

    def list_inventory(self, entity_id: str) -> list[dict]:
        return self._engine.list_inventory(entity_id)

    async def move_entity(
        self, entity_id: str, map_id: str, row: int, col: int
    ) -> None:
        """实体直接移动到坐标（行为驱动；实体放置/移除是地图编辑 admin 操作，B8）。"""
        await self._engine.move_entity(entity_id, map_id, row, col)

    async def set_attrs(self, entity_id: str, patch: dict) -> None:
        await self._engine.set_attrs(entity_id, patch)

    def get_attrs(self, entity_id: str) -> dict:
        return self._engine.get_attrs(entity_id)

    async def set_state(self, entity_id: str, patch: dict) -> None:
        await self._engine.set_state(entity_id, patch)

    def get_state(self, entity_id: str) -> dict:
        return self._engine.get_state(entity_id)

    async def say(self, entity_id: str, text: str, *, scope: str = "cell") -> None:
        await self._engine.say(entity_id, text, scope=scope)

    async def interact(
        self,
        entity_id: str,
        target_id: str,
        action: str,
        args: dict | None = None,
        item_id: str | None = None,
    ):
        return await self._engine.interact(entity_id, target_id, action, args, item_id)

    async def flush_item_defs(self) -> None:
        """（PlayLoader 内部使用）把注册的物品定义落库。"""
        await self._engine.flush_item_defs()
