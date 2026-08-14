"""v4.1 REST 公共：token 提取与鉴权（B4 token 三档）。

token 传递：``Authorization: Bearer <token>`` 或查询参数 ``?token=``
（SSE 的 EventSource 无法带 header，用查询参数）。
"""

from __future__ import annotations

from typing import Any

from astrbot.api.web import json_response, request

from ..world.identity import IdentityError, IdentityService, TokenInfo


class HttpAuthError(IdentityError):
    """鉴权错误（带 HTTP 状态码：401 未认证 / 403 权限不足）。"""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def token_from_request() -> str:
    """从请求提取 token（header 优先，其次查询参数）。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.query.get("token", "") or "").strip()


def auth(identity: IdentityService, *, tiers: tuple[str, ...] | None = None):
    """鉴权 helper：返回 ``(TokenInfo, None)`` 或 ``(None, 401/403 Response)``。

    Args:
        identity: 身份服务。
        tiers: 允许的档位（None = 任意有效凭据）。
    """
    info = identity.resolve(token_from_request())
    if info is None:
        return None, json_response({"error": "未认证或凭据无效"}, status_code=401)
    if tiers and info.tier not in tiers:
        return (
            None,
            json_response({"error": f"需要 {'/'.join(tiers)} 档凭据"}, status_code=403),
        )
    return info, None


def auth_guard(
    identity: IdentityService, *, tiers: tuple[str, ...] | None = None
) -> TokenInfo:
    """鉴权 helper（抛 HttpAuthError；handler 统一 error_response 返回）。"""
    info = identity.resolve(token_from_request())
    if info is None:
        raise HttpAuthError("未认证或凭据无效", 401)
    if tiers and info.tier not in tiers:
        raise HttpAuthError(f"需要 {'/'.join(tiers)} 档凭据", 403)
    return info


def error_response(exc: Exception) -> Any:
    """异常 → JSONResponse：HttpAuthError 按状态码（401/403），其余 400。"""
    if isinstance(exc, HttpAuthError):
        return json_response({"error": str(exc)}, status_code=exc.status_code)
    return json_response({"error": str(exc)}, status_code=400)
