"""地图编辑接口：地块 / 连接槽位 / 模板的增删改（可视化编辑器数据源）。

handler 只做类型校验（dict、字符串、整数坐标排除 bool 等），语义校验（地块是否
存在、方向合法性、重复 id 等）抛给引擎（WorldError → 400 error 信封）。update 按
payload 出现的键拼 kwargs。
"""

from __future__ import annotations

from astrbot.api.web import error_response, json_response, request

from ..world.engine import WorldError
from ..world.v3model import location_to_dict

_UNSET = object()


async def _body() -> dict:
    """读取并校验请求体必须为 JSON 对象。"""
    payload = await request.json(default=None)
    if not isinstance(payload, dict):
        raise WorldError("请求体必须是 JSON 对象")
    return payload


def _req_str(payload: dict, key: str, what: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise WorldError(f"{what}必须是字符串")
    return value


def _req_int(payload: dict, key: str, what: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorldError(f"{what}必须是整数")
    return value


def _opt_str(payload: dict, key: str, what: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise WorldError(f"{what}必须是字符串")
    return value


def _opt_bool(payload: dict, key: str) -> bool | object:
    if key not in payload:
        return _UNSET
    value = payload[key]
    if not isinstance(value, bool):
        raise WorldError(f"{key} 必须是布尔值")
    return value


def _coords(payload: dict) -> dict:
    """row/col 必填整数。"""
    return {
        "row": _req_int(payload, "row", "row"),
        "col": _req_int(payload, "col", "col"),
    }


def _map_kwargs(payload: dict) -> dict:
    return {"map_id": _opt_str(payload, "map_id", "map_id") or ""}


def _description_value(payload: dict) -> object:
    """description 键存在时：None=清空，str/dict=时段加权文本。"""
    if "description" not in payload:
        return _UNSET
    value = payload["description"]
    if value is None or isinstance(value, (str, dict)):
        return value
    raise WorldError("description 必须是字符串、时段对象或 null")


class EditAPI:
    """地图编辑：地块 / 连接槽位 / 模板（可视化编辑器数据源）。"""

    async def world_location_create(self):
        """新建地块。body: {map_id?, row, col, name?, description?, template_id?}"""
        try:
            payload = await _body()
            kwargs = {**_coords(payload), **_map_kwargs(payload)}
            kwargs["name"] = _opt_str(payload, "name", "地块名称")
            if "description" in payload:
                kwargs["description"] = _description_value(payload)
            if "template_id" in payload:
                kwargs["template_id"] = _req_str(payload, "template_id", "template_id")
            loc = await self.engine.create_location(**kwargs)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"location": location_to_dict(loc)})

    async def world_location_update(self):
        """更新地块（坐标只读）。body: {map_id?, row, col, name?, description?}"""
        try:
            payload = await _body()
            kwargs = {**_coords(payload), **_map_kwargs(payload)}
            if "name" in payload:
                kwargs["name"] = _req_str(payload, "name", "地块名称")
            if "description" in payload:
                kwargs["description"] = _description_value(payload)
            loc = await self.engine.update_location(**kwargs)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"location": location_to_dict(loc)})

    async def world_location_delete(self):
        """删除地块（级联清除指向它的目标，拒绝删除有玩家占据的地块）。body: {map_id?, row, col}"""
        try:
            payload = await _body()
            await self.engine.delete_location(
                **_coords(payload), **_map_kwargs(payload)
            )
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"ok": True})

    async def world_location_move(self):
        """移动地块（原子重写引用）。body: {map_id?, row, col, to_row, to_col}"""
        try:
            payload = await _body()
            to_row = _req_int(payload, "to_row", "to_row")
            to_col = _req_int(payload, "to_col", "to_col")
            loc = await self.engine.move_location(
                **_coords(payload), to_row=to_row, to_col=to_col, **_map_kwargs(payload)
            )
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"location": location_to_dict(loc)})

    async def world_connection_update(self):
        """更新连接槽位（方向不可改）。body: {map_id?, row, col, direction, enabled?, paths?}"""
        try:
            payload = await _body()
            direction = _req_str(payload, "direction", "direction")
            kwargs: dict = {**_coords(payload), **_map_kwargs(payload)}
            if "enabled" in payload:
                kwargs["enabled"] = _opt_bool(payload, "enabled")
            if "paths" in payload:
                kwargs["paths"] = _paths_value(payload["paths"])
            loc = await self.engine.update_connection(direction=direction, **kwargs)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"location": location_to_dict(loc)})

    async def world_template_create(self):
        """从源地块捕获模板。body: {id, name, map_id?, row, col}"""
        try:
            payload = await _body()
            template_id = _req_str(payload, "id", "模板 id")
            name = _req_str(payload, "name", "模板名称")
            tpl = await self.engine.create_template(
                template_id, name, **_coords(payload), **_map_kwargs(payload)
            )
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"template": {"id": tpl.id, "name": tpl.name}})

    async def world_template_update(self):
        """更新模板（改名或重新捕获）。body: {id, name?, map_id?, row?, col?}"""
        try:
            payload = await _body()
            template_id = _req_str(payload, "id", "模板 id")
            kwargs: dict = {}
            if "name" in payload:
                kwargs["name"] = _req_str(payload, "name", "模板名称")
            if "row" in payload or "col" in payload:
                kwargs.update(_coords(payload))
            if "map_id" in payload:
                kwargs["map_id"] = _opt_str(payload, "map_id", "map_id") or ""
            tpl = await self.engine.update_template(template_id, **kwargs)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"template": {"id": tpl.id, "name": tpl.name}})

    async def world_template_delete(self):
        """删除模板。body: {id}"""
        try:
            payload = await _body()
            template_id = _req_str(payload, "id", "模板 id")
            await self.engine.delete_template(template_id)
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"ok": True})

    async def world_template_apply(self):
        """应用模板到空地块。body: {id, map_id?, row, col}"""
        try:
            payload = await _body()
            template_id = _req_str(payload, "id", "模板 id")
            loc = await self.engine.apply_template(
                template_id, **_coords(payload), **_map_kwargs(payload)
            )
        except WorldError as e:
            return error_response(str(e), status_code=400)
        return json_response({"location": location_to_dict(loc)})


def _paths_value(value: object) -> list:
    if not isinstance(value, list):
        raise WorldError("paths 必须是数组")
    for p in value:
        if not isinstance(p, dict):
            raise WorldError("每条路径必须是对象")
        if not isinstance(p.get("targets"), list):
            raise WorldError("路径的 targets 必须是数组")
    return value
