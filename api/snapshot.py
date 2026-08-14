"""v4.1 只读状态快照端点（B10：REST 仅非动作）。

- ``/world/v4/state``：地图 + 地块 + 实体全量快照（read 档围观）。
- ``/world/v4/scene``：实体场景（read 档围观任意；play 档缺省自己）。
- ``/world/v4/bag``：实体背包（play 档自己；admin 可指定任意实体）。
"""

from __future__ import annotations

from astrbot.api.web import json_response, request

from ..world.identity import IdentityError
from ..world.v3model import location_to_dict, map_to_dict
from .v4common import auth_guard, error_response


def _entity_brief(e) -> dict:
    d = e.to_dict()
    return d


class V4SnapshotAPI:
    """世界只读快照（WebUI / 围观消费）。"""

    async def world_v4_state(self):
        """全量快照：maps / locations / entities（read 档）。"""
        info, err = _auth_read(self.identity)
        if err is not None:
            return err
        return json_response(
            {
                "maps": [map_to_dict(m) for m in self.v4_engine.list_maps()],
                "locations": [
                    location_to_dict(loc) for loc in self.v4_engine.list_locations()
                ],
                "entities": [_entity_brief(e) for e in self.v4_engine.list_entities()],
            }
        )

    async def world_v4_scene(self):
        """实体场景（read 档围观任意；play 档缺省自己的实体）。"""
        info, err = _auth_read(self.identity)
        if err is not None:
            return err
        entity_id = str(request.query.get("entity_id", "") or "").strip()
        if not entity_id:
            if info and info.entity_id:
                entity_id = info.entity_id
            else:
                return json_response({"error": "缺少 entity_id"}, status_code=400)
        entity = self.v4_engine.get_entity(entity_id)
        if entity is None:
            return json_response({"error": f"实体不存在：{entity_id}"}, status_code=404)
        try:
            scene = self.v4_engine._build_scene(entity)
            peers = [
                {
                    "entity": e.to_dict(),
                    "actions": [a.to_dict() for a in self.v4_engine.list_actions(e.id)],
                }
                for e in self.v4_engine.list_entities(
                    entity.map_id, entity.row, entity.col
                )
                if e.id != entity.id
            ]
        except IdentityError as e:
            return error_response(e)
        return json_response(
            {
                "entity": _entity_brief(entity),
                "scene": scene.to_dict(),
                "peers": peers,
            }
        )

    async def world_v4_bag(self):
        """实体背包（play 档自己；admin 可指定 entity_id）。"""
        info, err = _auth_play(self.identity)
        if err is not None:
            return err
        entity_id = str(request.query.get("entity_id", "") or "").strip()
        if not entity_id:
            entity_id = info.entity_id
        elif info.tier != "admin":
            return json_response({"error": "只能查看自己的背包"}, status_code=403)
        entity = self.v4_engine.get_entity(entity_id)
        if entity is None:
            return json_response({"error": f"实体不存在：{entity_id}"}, status_code=404)
        return json_response(
            {
                "entity_id": entity_id,
                "items": self.v4_engine.list_inventory(entity_id),
            }
        )


def _auth_read(identity):
    """read+ 鉴权（围观）。"""
    try:
        info = auth_guard(identity, tiers=("read", "play", "admin"))
    except IdentityError as e:
        return None, error_response(e)
    return info, None


def _auth_play(identity):
    """play+ 鉴权（动作/私密数据）。"""
    try:
        info = auth_guard(identity, tiers=("play", "admin"))
    except IdentityError as e:
        return None, error_response(e)
    return info, None
