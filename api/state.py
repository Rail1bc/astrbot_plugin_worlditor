"""世界状态接口：全量地图 + 地块（含连接槽位）+ 模板 + 玩家场景 + agent 位置。"""

from __future__ import annotations

from astrbot.api.web import json_response, request

from ..world.engine import AGENT_PLAYER_ID
from ..world.store import DEFAULT_MAP_ID
from ..world.v3model import location_to_dict, map_to_dict


class StateAPI:
    """世界状态（地图渲染 + 当前场景），插件页调试工具的数据源。"""

    async def world_state(self):
        """返回全量地图与指定玩家的场景。

        Query:
            player_id（可选）: 玩家 id；存在则附带该玩家场景，否则 ``player`` 为 null。
        """
        player_id = request.query.get("player_id", "").strip()
        locations = [
            location_to_dict(loc) for loc in self.engine.list_locations()
        ]
        maps = [map_to_dict(m) for m in self.engine.list_maps()]

        player = None
        if player_id:
            scene = await self.engine.describe_scene(player_id)
            if scene is not None:
                p = self.engine.get_player(player_id)
                player = {
                    "player_id": p.player_id,
                    "name": p.name,
                    "map_id": p.map_id,
                    "row": p.row,
                    "col": p.col,
                    "scene": scene.to_dict(),
                }

        agent = None
        agent_player = self.engine.get_player(AGENT_PLAYER_ID)
        if agent_player is not None:
            agent = {
                "player_id": agent_player.player_id,
                "name": agent_player.name,
                "map_id": agent_player.map_id,
                "row": agent_player.row,
                "col": agent_player.col,
            }

        # 出生点：默认地图的 spawn 坐标（被删则回落第一张地图）
        spawn_map = next(
            (m for m in self.engine.list_maps() if m.id == DEFAULT_MAP_ID),
            None,
        ) or next(iter(self.engine.list_maps()), None)
        spawn = (
            {"map_id": spawn_map.id, "row": spawn_map.spawn_row, "col": spawn_map.spawn_col}
            if spawn_map
            else {"map_id": "", "row": 0, "col": 0}
        )
        return json_response(
            {
                "maps": maps,
                "locations": locations,
                "templates": [
                    {"id": t.id, "name": t.name}
                    for t in self.engine.list_templates()
                ],
                "player": player,
                "agent": agent,
                "spawn": {
                    "map_id": spawn[0],
                    "row": spawn[1],
                    "col": spawn[2],
                },
            }
        )
