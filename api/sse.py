"""v4.1 SSE 事件流端点（B11：实时感知走 SSE，不轮询 MCP）。

``GET /world/v4/events?token=<play 档凭据>``：浏览器 EventSource 原生消费；
SSE 是事件总线（引擎 ``subscribe``）的序列化出口——事件驱动增量更新，
断线重连后由 WebUI 拉快照兜底。
"""

from __future__ import annotations

import json

from starlette.responses import StreamingResponse

from ..world.identity import IdentityError
from .v4common import auth_guard, error_response


class V4SseAPI:
    """世界事件流（play 档）。"""

    async def world_v4_events(self):
        """SSE 流：公共 6 事件（say/move/enter/interact/changed/edited）推送。"""
        try:
            auth_guard(self.identity, tiers=("play", "admin"))
        except IdentityError as e:
            return error_response(e)
        queue = self.v4_engine.subscribe()

        async def event_gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    payload = await queue.get()
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                self.v4_engine.unsubscribe(queue)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
