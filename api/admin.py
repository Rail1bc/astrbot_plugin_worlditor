"""v4.1 admin 管理端点（B10：REST 仅非动作；admin 档 token）。

地图编辑（地块 / 连接 / 地图 / 实体放置与编辑）全部走 v4 引擎原语
（B8：实体 = 地图编辑内容，由 admin 放置；玩法包只有行为扩展）。
"""

from __future__ import annotations

from astrbot.api.web import json_response, request

from ..world.identity import IdentityError
from ..world.v4engine import _UNSET, WorldError
from .v4common import auth_guard, error_response


class V4AdminAPI:
    """世界管理端点（admin 档凭据）。"""

    def _admin_guard(self):
        """admin 档鉴权（同步；抛 HttpAuthError → error_response 403）。"""
        return auth_guard(self.identity, tiers=("admin",))

    async def world_v4_admin_location_create(self):
        """新建地块（map_id / row / col / name；description 可选）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            loc = await self.v4_engine.create_location(
                data.get("map_id", ""),
                data.get("row"),
                data.get("col"),
                data.get("name", ""),
                description=data.get("description"),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        from ..world.v3model import location_to_dict

        return json_response({"ok": True, "location": location_to_dict(loc)})

    async def world_v4_admin_location_update(self):
        """更新地块名称 / 描述。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            loc = await self.v4_engine.update_location(
                data.get("map_id", ""),
                data.get("row"),
                data.get("col"),
                name=data.get("name", _UNSET),
                description=data.get("description", _UNSET),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        from ..world.v3model import location_to_dict

        return json_response({"ok": True, "location": location_to_dict(loc)})

    async def world_v4_admin_location_delete(self):
        """删除地块（级联删实体与引用；有玩家在场拒绝）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            await self.v4_engine.delete_location(
                data.get("map_id", ""), data.get("row"), data.get("col")
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        return json_response({"ok": True})

    async def world_v4_admin_location_move(self):
        """移动地块（原子重写全图引用）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            loc = await self.v4_engine.move_location(
                data.get("map_id", ""),
                data.get("row"),
                data.get("col"),
                data.get("to_row"),
                data.get("to_col"),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        from ..world.v3model import location_to_dict

        return json_response({"ok": True, "location": location_to_dict(loc)})

    async def world_v4_admin_connection_update(self):
        """更新地块某方向连接槽位（enabled / paths 整体替换）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            loc = await self.v4_engine.update_connection(
                data.get("map_id", ""),
                data.get("row"),
                data.get("col"),
                data.get("direction", ""),
                enabled=data.get("enabled", _UNSET),
                paths=data.get("paths", _UNSET),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        from ..world.v3model import location_to_dict

        return json_response({"ok": True, "location": location_to_dict(loc)})

    async def world_v4_admin_map_create(self):
        """新建地图（多图前端支持）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            m = await self.v4_engine.create_map(
                data.get("id", ""),
                data.get("name", ""),
                description=data.get("description"),
                timezone=data.get("timezone"),
                spawn_row=data.get("spawn_row", 0),
                spawn_col=data.get("spawn_col", 0),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        from ..world.v3model import map_to_dict

        return json_response({"ok": True, "map": map_to_dict(m)})

    async def world_v4_admin_map_update(self):
        """更新地图属性。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            m = await self.v4_engine.update_map(
                data.get("id", ""),
                name=data.get("name", _UNSET),
                description=data.get("description", _UNSET),
                timezone=data.get("timezone", _UNSET),
                spawn_row=data.get("spawn_row", _UNSET),
                spawn_col=data.get("spawn_col", _UNSET),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        from ..world.v3model import map_to_dict

        return json_response({"ok": True, "map": map_to_dict(m)})

    async def world_v4_admin_entity_place(self):
        """放置实体（kind / map_id / row / col；name/desc/attrs/state 可选）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            entity = await self.v4_engine.place_entity(
                data.get("kind", ""),
                data.get("map_id", ""),
                data.get("row"),
                data.get("col"),
                name=data.get("name"),
                desc=data.get("desc", ""),
                attrs=data.get("attrs"),
                state=data.get("state"),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        return json_response({"ok": True, "entity": entity.to_dict()})

    async def world_v4_admin_entity_remove(self):
        """移除实体（级联清理背包）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            await self.v4_engine.remove_entity(data.get("entity_id", ""))
        except (IdentityError, WorldError) as e:
            return error_response(e)
        return json_response({"ok": True})

    async def world_v4_admin_entity_update(self):
        """更新实体（name/desc/attrs/state 可选；attrs/state 整体替换）。"""
        try:
            self._admin_guard()
            data = await request.json() or {}
            entity = await self.v4_engine.update_entity(
                data.get("entity_id", ""),
                name=data.get("name", _UNSET),
                desc=data.get("desc", _UNSET),
                attrs=data.get("attrs", _UNSET),
                state=data.get("state", _UNSET),
            )
        except (IdentityError, WorldError) as e:
            return error_response(e)
        return json_response({"ok": True, "entity": entity.to_dict()})
