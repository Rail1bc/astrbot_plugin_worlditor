"""世界状态接口：全量地图 + 玩家场景 + agent 位置。"""

from __future__ import annotations

from astrbot.api.web import json_response, request

from ..world.engine import AGENT_PLAYER_ID


class StateAPI:
    """世界状态（地图渲染 + 当前场景），插件页调试工具的数据源。"""

    async def world_state(self):
        """返回全量地图（locations + exits）与指定玩家的场景。

        Query:
            player_id（可选）: 玩家 id；存在则附带该玩家场景，否则 ``player`` 为 null。
        """
        player_id = request.query.get("player_id", "").strip()
        locations = [loc.as_dict() for loc in self.engine.list_locations()]
        exits = [e.as_dict() for e in self.engine.list_all_exits()]

        player = None
        if player_id:
            scene = await self.engine.describe_scene(player_id)
            if scene is not None:
                p = self.engine.get_player(player_id)
                player = {
                    "player_id": p.player_id,
                    "name": p.name,
                    "location_id": p.location_id,
                    "location_name": scene.location.name,
                    "scene": scene.as_dict(),
                }

        agent = None
        agent_player = self.engine.get_player(AGENT_PLAYER_ID)
        if agent_player is not None:
            agent_loc = self.engine.get_location(agent_player.location_id)
            agent = {
                "player_id": agent_player.player_id,
                "name": agent_player.name,
                "location_id": agent_player.location_id,
                "location_name": agent_loc.name if agent_loc else None,
            }

        return json_response(
            {
                "locations": locations,
                "exits": exits,
                "player": player,
                "agent": agent,
            }
        )
