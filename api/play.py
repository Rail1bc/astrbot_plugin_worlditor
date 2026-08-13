"""玩家动作接口：注册 / 移动 / 注销（隐形实体，仅内存）。"""

from __future__ import annotations

import uuid

from astrbot.api.web import error_response, json_response, request

from ..world.engine import WorldError


class PlayAPI:
    """隐形玩家的注册 / 移动 / 注销。

    人类玩家随机 id 匿名，不依赖 dashboard 用户名；页面把 player_id 随请求
    携带，刷新后重新注册新 id，旧实体由超时清理兜底。
    """

    async def world_register(self):
        """注册一个隐形玩家，返回随机 player_id 与起始地块。"""
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            payload = {}
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            return error_response("name 必须是字符串", status_code=400)
        name = (name or "").strip()[:32] or None
        player_id = uuid.uuid4().hex[:8]
        loc = await self.engine.register_player(player_id, name)
        return json_response(
            {
                "player_id": player_id,
                "map_id": loc.map_id,
                "row": loc.row,
                "col": loc.col,
                "location_name": loc.name,
            }
        )

    async def world_move(self):
        """按方向移动玩家，返回新场景。

        body: {player_id, direction, path?, target?}
        ``path`` 为槽内路径索引（多路径时必填）；``target`` 为 {map_id?, row, col}，
        指定时直取该坐标（须为路径目标之一）。
        """
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        player_id = str(payload.get("player_id") or "").strip()
        direction = str(payload.get("direction") or "").strip()
        if not player_id or not direction:
            return error_response("player_id 与 direction 均为必填", status_code=400)
        path = payload.get("path")
        if path is not None and not isinstance(path, int):
            return error_response("path 必须是整数", status_code=400)
        target = payload.get("target")
        if target is not None and not isinstance(target, dict):
            return error_response("target 必须是坐标对象", status_code=400)
        try:
            scene = await self.engine.move(
                player_id, direction, path=path, target=target
            )
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response(scene.to_dict())

    async def world_deregister(self):
        """注销玩家（agent 不可注销；页面 unload 尽力调用，超时清理兜底）。"""
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        player_id = str(payload.get("player_id") or "").strip()
        if not player_id:
            return error_response("player_id 为必填", status_code=400)
        await self.engine.deregister_player(player_id)
        return json_response({"ok": True})
