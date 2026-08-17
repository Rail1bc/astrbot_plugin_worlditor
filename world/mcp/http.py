"""世界服务（v4.1，独立端口 6288；B7 修订为独立 uvicorn 服务）。

WebUI 与远程 MCP 客户端的**单一连接源**：

- ``/world/mcp``：MCP streamable HTTP（唯一动作通道，B10）
- ``/state`` / ``/scene``（含实体可用动作）/ ``/bag``：只读快照
- ``/events``：SSE 事件流（事件总线序列化出口，B11）
- ``/auth/*``：身份注册 / 登录 / agent 注册 / read-token / 注销 / 改密（B13）
- ``/plays/<id>/web/*``：玩法包 web 静态资源（B9）

鉴权：AuthMiddleware 校验 ``Authorization: Bearer <token>`` 或 ``?token=``，
身份存入 scope.state（handler 与 MCP _meta 注入共用）；公共路径（注册类）
白名单放行。CORS 按配置的 allowed_origins 放行（WebUI 独立域）。
"""

from __future__ import annotations

import json
import mimetypes
import urllib.parse
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from ..identity import IdentityError, IdentityService, TokenInfo

_META_ENTITY_KEY = "worlditor_entity_id"
_META_TIER_KEY = "worlditor_tier"
_SCOPE_IDENTITY_KEY = "worlditor_identity"

# 公共路径（免认证）：自助注册类端点（B13 自助注册不依赖管理员）
PUBLIC_PATHS = (
    "/auth/register",
    "/auth/login",
    "/auth/agent-register",
    "/auth/read-token",
)


def _bearer_token(authorization: str) -> str:
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _query_token(scope: dict) -> str:
    query = scope.get("query_string", b"").decode("latin-1")
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "token":
            return urllib.parse.unquote(value)
    return ""


class AuthMiddleware:
    """ASGI 认证中间件：校验 token、身份入 scope、注入 MCP 请求 _meta。

    Args:
        public_paths: 前缀匹配免认证的路径（注册类端点）。
        public_exact: 精确匹配免认证的路径（如 "/" 内置 WebUI 首页）。
    """

    def __init__(
        self,
        app: Any,
        identity: IdentityService,
        *,
        public_paths: tuple[str, ...] = (),
        public_exact: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.identity = identity
        self.public_paths = public_paths
        self.public_exact = public_exact

    @property
    def state(self) -> Any:
        """转发内部 Starlette app 的 state（handler 经 request.app.state 取依赖）。"""
        return self.app.state

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in self.public_exact or any(
            path == p or path.startswith(p) for p in self.public_paths
        ):
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        token = _bearer_token(headers.get("authorization", "")) or _query_token(scope)
        info = self.identity.resolve(token) if token else None
        if info is None:
            await _send_json(send, scope, 401, {"error": "未认证或凭据无效"})
            return
        scope.setdefault("state", {})[_SCOPE_IDENTITY_KEY] = info
        if scope["method"] == "POST":
            receive = self._wrap_receive(receive, info)
        await self.app(scope, receive, send)

    def _wrap_receive(self, receive: Any, info: Any) -> Any:
        """包装 receive：合并 body 并把身份注入 params._meta。"""

        async def wrapped():
            chunks = []
            more = True
            while more:
                message = await receive()
                chunks.append(message.get("body") or b"")
                more = bool(message.get("more_body", False))
            body = _inject_identity(b"".join(chunks), info)
            return {"type": "http.request", "body": body, "more_body": False}

        return wrapped


def _inject_identity(body: bytes, info: Any) -> bytes:
    """JSON-RPC 单请求：在 params._meta 注入 worlditor 身份字段。"""
    if not body:
        return body
    try:
        obj = json.loads(body)
    except ValueError:
        return body
    if not isinstance(obj, dict):
        return body  # 批处理 / 非标准请求：跳过注入
    params = obj.get("params")
    if isinstance(params, dict):
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
        meta[_META_ENTITY_KEY] = info.entity_id
        meta[_META_TIER_KEY] = info.tier
        params["_meta"] = meta
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return body


async def _send_json(send: Any, scope: dict, status: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---------- 业务端点（WebUI 数据源；鉴权经中间件 + tier 检查） ----------


def _identity_of(scope: dict) -> TokenInfo | None:
    return scope.get("state", {}).get(_SCOPE_IDENTITY_KEY)


def _require_tier(info: TokenInfo | None, tiers: tuple[str, ...]) -> None:
    if info is None:
        raise HTTPException(401, "未认证或凭据无效")
    if info.tier not in tiers:
        raise HTTPException(403, f"需要 {'/'.join(tiers)} 档凭据")


async def _state(request: Request) -> Response:
    _require_tier(_identity_of(request.scope), ("read", "play", "admin"))
    engine = request.app.state.world_engine
    from ..v3model import location_to_dict, map_to_dict

    return JSONResponse(
        {
            "maps": [map_to_dict(m) for m in engine.list_maps()],
            "locations": [location_to_dict(loc) for loc in engine.list_locations()],
            "entities": [e.to_dict() for e in engine.list_entities()],
        }
    )


async def _scene(request: Request) -> Response:
    _require_tier(_identity_of(request.scope), ("read", "play", "admin"))
    engine = request.app.state.world_engine
    info = _identity_of(request.scope)
    entity_id = request.query_params.get("entity_id") or (
        info.entity_id if info else ""
    )
    if not entity_id:
        raise HTTPException(400, "缺少 entity_id")
    entity = engine.get_entity(entity_id)
    if entity is None:
        raise HTTPException(404, f"实体不存在：{entity_id}")
    scene = engine._build_scene(entity)
    peers = [
        p
        for p in engine.list_entities(entity.map_id, entity.row, entity.col)
        if p.id != entity.id
    ]
    return JSONResponse(
        {
            "entity": {
                **entity.to_dict(),
                "actions": [a.to_dict() for a in engine.list_actions(entity.id)],
            },
            "scene": scene.to_dict(),
            "peers": [
                {
                    "entity": p.to_dict(),
                    "actions": [a.to_dict() for a in engine.list_actions(p.id)],
                }
                for p in peers
            ],
        }
    )


async def _bag(request: Request) -> Response:
    info = _identity_of(request.scope)
    _require_tier(info, ("play", "admin"))
    engine = request.app.state.world_engine
    entity_id = request.query_params.get("entity_id") or info.entity_id
    if not entity_id:
        raise HTTPException(400, "缺少 entity_id")
    if entity_id != info.entity_id and info.tier != "admin":
        raise HTTPException(403, "只能查看自己的背包")
    if engine.get_entity(entity_id) is None:
        raise HTTPException(404, f"实体不存在：{entity_id}")
    return JSONResponse(
        {"entity_id": entity_id, "items": engine.list_inventory(entity_id)}
    )


async def _events(request: Request) -> Response:
    _require_tier(_identity_of(request.scope), ("play", "admin"))
    engine = request.app.state.world_engine
    queue = engine.subscribe()

    async def event_gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                payload = await queue.get()
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            engine.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _auth_body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "请求体必须是 JSON") from None
    return data if isinstance(data, dict) else {}


def _identity_error_response(exc: Exception) -> Response:
    return JSONResponse({"error": str(exc)}, status_code=400)


async def _register(request: Request) -> Response:
    identity: IdentityService = request.app.state.world_identity
    data = await _auth_body(request)
    try:
        info = await identity.register_human(
            str(data.get("username") or ""),
            str(data.get("password") or ""),
            invite_code=data.get("invite_code"),
            admin_key=data.get("admin_key"),
        )
    except IdentityError as e:
        return _identity_error_response(e)
    return JSONResponse({"ok": True, "token": info.to_dict()})


async def _login(request: Request) -> Response:
    identity: IdentityService = request.app.state.world_identity
    data = await _auth_body(request)
    try:
        info = await identity.login(
            str(data.get("username") or ""), str(data.get("password") or "")
        )
    except IdentityError as e:
        return _identity_error_response(e)
    return JSONResponse({"ok": True, "token": info.to_dict()})


async def _agent_register(request: Request) -> Response:
    identity: IdentityService = request.app.state.world_identity
    data = await _auth_body(request)
    try:
        info = await identity.register_agent(
            str(data.get("name") or ""), invite_code=data.get("invite_code")
        )
    except IdentityError as e:
        return _identity_error_response(e)
    return JSONResponse({"ok": True, "token": info.to_dict()})


async def _read_token(request: Request) -> Response:
    identity: IdentityService = request.app.state.world_identity
    try:
        info = await identity.create_read_token()
    except IdentityError as e:
        return _identity_error_response(e)
    return JSONResponse({"ok": True, "token": info.to_dict()})


async def _logout(request: Request) -> Response:
    identity: IdentityService = request.app.state.world_identity
    info = _identity_of(request.scope)
    ok = await identity.logout(info.token) if info else False
    return JSONResponse({"ok": ok})


async def _change_password(request: Request) -> Response:
    identity: IdentityService = request.app.state.world_identity
    info = _identity_of(request.scope)
    _require_tier(info, ("play", "admin"))
    data = await _auth_body(request)
    try:
        await identity.change_password(
            info.token,
            str(data.get("old_password") or ""),
            str(data.get("new_password") or ""),
        )
    except IdentityError as e:
        return _identity_error_response(e)
    return JSONResponse({"ok": True})


async def _play_web(request: Request) -> Response:
    _require_tier(_identity_of(request.scope), ("read", "play", "admin"))
    loader = request.app.state.world_loader
    play_id = request.path_params.get("play_id", "")
    subpath = request.path_params.get("path", "")
    info = loader.plays.get(play_id) if loader else None
    if info is None:
        raise HTTPException(404, f"玩法包不存在：{play_id}")
    web_dir = (info.path / "web").resolve()
    target = (web_dir / subpath).resolve()
    if not str(target).startswith(str(web_dir)) or not target.is_file():
        raise HTTPException(404, "资源不存在")
    content = target.read_bytes()
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(content=content, media_type=media_type)


# ---------- 组装 ----------


def build_http_app(
    mcp: FastMCP,
    identity: IdentityService,
    *,
    engine: Any = None,
    loader: Any = None,
    allowed_origins: list[str] | None = None,
    static_dir: Any = None,
) -> Any:
    """世界服务 ASGI app：MCP streamable HTTP + 业务端点 + 内置 WebUI + 认证 + CORS。

    Args:
        mcp: FastMCP 实例（工具已注册）。
        identity: 身份服务。
        engine: V4WorldEngine（快照/SSE 数据源）。
        loader: PlayLoader（玩法包 web 资源）。
        allowed_origins: CORS 允许的来源（WebUI 独立部署域；内置托管时同源可留空）。
        static_dir: WebUI 构建产物目录（webui/dist）；存在时挂载为根路径静态资源
            （免认证——登录页需在未认证时加载）。
    """
    mcp_starlette = mcp.streamable_http_app()
    routes: list = list(mcp_starlette.routes) + [
        Route("/state", _state),
        Route("/scene", _scene),
        Route("/bag", _bag),
        Route("/events", _events),
        Route("/auth/register", _register, methods=["POST"]),
        Route("/auth/login", _login, methods=["POST"]),
        Route("/auth/agent-register", _agent_register, methods=["POST"]),
        Route("/auth/read-token", _read_token, methods=["GET"]),
        Route("/auth/logout", _logout, methods=["POST"]),
        Route("/auth/change-password", _change_password, methods=["POST"]),
        Route("/plays/{play_id}/web/{path:path}", _play_web, methods=["GET"]),
    ]
    # 合并 FastMCP app 的 lifespan（session manager / task group 初始化）——
    # 只复制 routes 会丢失 lifespan，导致 MCP 请求 500
    from contextlib import asynccontextmanager

    mcp_lifespan = mcp_starlette.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app):
        async with mcp_lifespan(app):
            yield

    # 内置托管：WebUI 构建产物挂根路径（API 路由优先；SPA 由 hash 路由承担，
    # 服务器只需 index.html 与 /assets/*）
    public_exact: tuple[str, ...] = ()
    if static_dir is not None:
        dist = Path(static_dir)
        if dist.is_dir():
            from starlette.staticfiles import StaticFiles

            routes.append(
                Mount(
                    "/", app=StaticFiles(directory=str(dist), html=True), name="webui"
                )
            )
            public_exact = ("/",)
            public_paths = PUBLIC_PATHS + ("/assets", "/favicon.ico")
        else:
            public_paths = PUBLIC_PATHS
    else:
        public_paths = PUBLIC_PATHS

    app = Starlette(routes=routes, lifespan=combined_lifespan)
    app.state.world_engine = engine
    app.state.world_identity = identity
    app.state.world_loader = loader
    app = AuthMiddleware(
        app, identity, public_paths=public_paths, public_exact=public_exact
    )
    if allowed_origins:
        app = CORSMiddleware(
            app,
            allow_origins=allowed_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )
    return app


class WorldHttpServer:
    """独立 uvicorn 服务（main.py 装配：enable_world_api 时启动）。"""

    def __init__(self, app: Any, *, host: str, port: int) -> None:
        self._app = app
        self.host = host
        self.port = port
        self._server: Any = None

    async def start(self) -> None:
        """启动服务（在 asyncio task 中运行，阻塞直到退出）。"""
        import uvicorn

        config = uvicorn.Config(
            self._app, host=self.host, port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        await self._server.serve()

    def stop(self) -> None:
        """请求服务退出（配合 await 启动 task）。"""
        if self._server is not None:
            self._server.should_exit = True
