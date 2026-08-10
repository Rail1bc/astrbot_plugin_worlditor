"""地图编辑接口：地块 / 出口的增删改（可视化编辑器数据源）。

handler 只做类型校验（dict、字符串、layout 数字排除 bool、reveal_target 布尔、
direction 字符串），语义校验（地块/出口是否存在、方向合法性、重复 id 等）抛给
引擎（WorldError → 400 error 信封）。update 按 payload 出现的键拼 kwargs。
"""

from __future__ import annotations

from astrbot.api.web import error_response, json_response, request

from ..world.engine import WorldError


async def _body() -> dict:
    """读取并校验请求体必须为 JSON 对象。"""
    payload = await request.json(default=None)
    if not isinstance(payload, dict):
        raise WorldError("请求体必须是 JSON 对象")
    return payload


def _require_str(payload: dict, key: str, what: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise WorldError(f"{what}必须是字符串")
    return value


def _opt_str(payload: dict, key: str, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise WorldError(f"{key} 必须是字符串")
    return value


def _opt_bool(payload: dict, key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise WorldError(f"{key} 必须是布尔值")
    return value


def _layout_args(payload: dict) -> dict:
    """把 layout 键展开为引擎坐标 kwargs。

    缺省=不传（create 默认无坐标，update 保持不变）；null=显式清空坐标；
    {x, y}=更新坐标（x/y 必须同时提供，排除 bool）。
    """
    if "layout" not in payload:
        return {}
    value = payload["layout"]
    if value is None:
        return {"layout_x": None, "layout_y": None}
    if not isinstance(value, dict):
        raise WorldError("layout 必须是 {x, y} 对象或 null")
    x, y = value.get("x"), value.get("y")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
    ):
        raise WorldError("layout 的 x/y 必须是数字")
    return {"layout_x": x, "layout_y": y}


class EditAPI:
    """地图编辑：地块 / 出口的增删改（可视化编辑器数据源）。"""

    async def world_location_create(self):
        """新建地块。body: {id, name, description?, layout?}"""
        try:
            payload = await _body()
            loc_id = _require_str(payload, "id", "地块 id")
            name = _require_str(payload, "name", "地块名称")
            description = payload.get("description")
            if description is not None and not isinstance(description, str):
                raise WorldError("description 必须是字符串")
            loc = await self.engine.create_location(
                loc_id, name, description or "", **_layout_args(payload)
            )
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"location": loc.as_dict()})

    async def world_location_update(self):
        """更新地块。body: {id, name?, description?, layout?}，layout: null=清坐标。"""
        try:
            payload = await _body()
            loc_id = _require_str(payload, "id", "地块 id")
            kwargs: dict = {}
            if "name" in payload:
                kwargs["name"] = _require_str(payload, "name", "地块名称")
            if "description" in payload:
                desc = payload["description"]
                if desc is not None and not isinstance(desc, str):
                    raise WorldError("description 必须是字符串")
                kwargs["description"] = desc
            kwargs.update(_layout_args(payload))
            loc = await self.engine.update_location(loc_id, **kwargs)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"location": loc.as_dict()})

    async def world_location_delete(self):
        """删除地块（级联删出边，拒绝删除有玩家占据的地块）。body: {id}"""
        try:
            payload = await _body()
            loc_id = _require_str(payload, "id", "地块 id")
            await self.engine.delete_location(loc_id)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"ok": True})

    async def world_exit_create(self):
        """新建出口。body: {id, from_id, to_id, label, reveal_target?, direction?}"""
        try:
            payload = await _body()
            exit_id = _require_str(payload, "id", "出口 id")
            from_id = _require_str(payload, "from_id", "from_id")
            to_id = _require_str(payload, "to_id", "to_id")
            label = _require_str(payload, "label", "出口标签")
            exit_ = await self.engine.create_exit(
                exit_id,
                from_id,
                to_id,
                label,
                reveal_target=_opt_bool(payload, "reveal_target", True),
                direction=_opt_str(payload, "direction", "up"),
            )
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"exit": exit_.as_dict()})

    async def world_exit_update(self):
        """更新出口（from_id 不可变）。body: {id, to_id?, label?, reveal_target?, direction?}"""
        try:
            payload = await _body()
            exit_id = _require_str(payload, "id", "出口 id")
            kwargs: dict = {}
            if "to_id" in payload:
                kwargs["to_id"] = _require_str(payload, "to_id", "to_id")
            if "label" in payload:
                kwargs["label"] = _require_str(payload, "label", "出口标签")
            if "reveal_target" in payload:
                kwargs["reveal_target"] = _opt_bool(payload, "reveal_target", True)
            if "direction" in payload:
                kwargs["direction"] = _opt_str(payload, "direction", "up")
            exit_ = await self.engine.update_exit(exit_id, **kwargs)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"exit": exit_.as_dict()})

    async def world_exit_delete(self):
        """删除一条出口。body: {id}"""
        try:
            payload = await _body()
            exit_id = _require_str(payload, "id", "出口 id")
            await self.engine.delete_exit(exit_id)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"ok": True})
