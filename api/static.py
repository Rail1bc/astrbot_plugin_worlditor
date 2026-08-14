"""玩法包 web 静态资源托管（B9，v4.1）。

``GET /world/v4/plays/<play_id>/web/<path>``（read 档 token）：托管玩法包
``web/`` 目录下的自定义界面组件资源；WebUI 以 import map 注册后动态 import。
路径穿越防护：解析后必须位于玩法包 web/ 目录内。
"""

from __future__ import annotations

import mimetypes

from astrbot.api.web import json_response, request
from starlette.responses import Response

from ..world.identity import IdentityError
from .v4common import auth_guard


class V4StaticAPI:
    """玩法包 web 资源（read 档可访问——组件资源渲染需要）。"""

    async def world_v4_play_web(self):
        """读取玩法包 web/ 下静态资源。"""
        try:
            auth_guard(self.identity, tiers=("read", "play", "admin"))
        except IdentityError as e:
            return json_response({"error": str(e)}, status_code=401)
        play_id = request.path_params.get("play_id", "")
        subpath = request.path_params.get("path", "")
        info = self.play_loader.plays.get(play_id) if self.play_loader else None
        if info is None:
            return json_response({"error": f"玩法包不存在：{play_id}"}, status_code=404)
        web_dir = (info.path / "web").resolve()
        target = (web_dir / subpath).resolve()
        if not str(target).startswith(str(web_dir)) or not target.is_file():
            return json_response({"error": "资源不存在"}, status_code=404)
        content = target.read_bytes()
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Response(content=content, media_type=media_type)
