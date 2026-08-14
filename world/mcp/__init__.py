"""进程内 MCP server（v4.1，B7 / B10 / B11）。

工具 = 引擎动作原语的薄封装（协议无关层零改动）；返回**结构化 JSON**
``{text, ui, effects}``——agent 消费 ``text``，WebUI 渲染 ``ui``，一次实现
两端复用。

连接身份验证（token → 实体）：
- HTTP（streamable HTTP）：认证中间件校验 ``Authorization: Bearer <token>``，
  把 ``{entity_id, tier}`` 注入每个 JSON-RPC 请求的 ``params._meta``，
  工具经 ``ctx.request_context.meta`` 读取（read 档无实体 → 工具不可用）。
- stdio（本地）：启动时经 ``--token`` / 环境变量绑定固定实体。

工具集（v4.1 初版）：world_look / world_move / world_say / world_bag /
world_use / world_interact / world_who。
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..v4engine import WorldError

# MCP 工具返回：结构化 JSON 字符串（ensure_ascii=False，LLM/UI 双端消费）
_META_ENTITY_KEY = "worlditor_entity_id"
_META_TIER_KEY = "worlditor_tier"


class McpAuthError(Exception):
    """MCP 连接/身份错误。"""


def _result(payload: dict) -> str:
    """结构化返回：``{text, ...}`` 序列化为 JSON 字符串。"""
    return json.dumps(payload, ensure_ascii=False)


def _entity_id(ctx: Context, fixed_identity: Any = None) -> str:
    """从连接身份解析实体 id（HTTP 读 _meta；stdio 用固定身份）。"""
    if fixed_identity is not None:
        return fixed_identity.entity_id
    meta = None
    try:
        meta = ctx.request_context.meta if ctx.request_context else None
    except ValueError:  # Context 不在请求内（进程内 call_tool 等场景）
        meta = None
    entity_id = getattr(meta, _META_ENTITY_KEY, None)
    if not entity_id:
        raise McpAuthError("连接未认证或凭据只能围观，无法执行动作")
    return entity_id


def build_mcp_server(engine: Any, fixed_identity: Any = None) -> FastMCP:
    """构建 worlditor MCP server（工具 = 引擎原语薄封装）。

    Args:
        engine: V4WorldEngine 实例。
        fixed_identity: stdio 模式绑定的固定身份（TokenInfo）；HTTP 模式传 None，
            身份经请求 _meta 注入。

    Returns:
        配置好 7 个世界工具的 FastMCP 实例。
    """
    mcp = FastMCP(
        "worlditor",
        instructions=(
            "你是一个生活在 worlditor 世界中的实体。使用 world_look 查看当前位置，"
            "world_move 移动，world_say 说话，world_bag 查看背包，world_use 使用物品，"
            "world_interact 与世界中的实体交互，world_who 查看同地块的实体。"
            "所有工具返回 JSON：text 字段是给 LLM 的文本，ui 字段是界面结构（忽略即可）。"
        ),
        streamable_http_path="/world/mcp",
    )

    # ---------- 场景（world_look / world_who） ----------

    @mcp.tool()
    async def world_look(ctx: Context) -> str:
        """查看你当前所在位置：场景描述、可移动方向与同地块实体。"""
        try:
            entity_id = _entity_id(ctx, fixed_identity)
            entity = engine._require_entity(entity_id)
            scene = engine._build_scene(entity)
            peers = _peers(engine, entity)
            text = _scene_text(scene, peers)
            return _result(
                {
                    "text": text,
                    "scene": scene.to_dict(),
                    "entities": [e.to_dict() for e in peers],
                }
            )
        except (WorldError, McpAuthError) as e:
            return _result({"text": str(e)})

    @mcp.tool()
    async def world_who(ctx: Context) -> str:
        """查看与你同在地块的实体（名称 / kind / 描述）。"""
        try:
            entity_id = _entity_id(ctx, fixed_identity)
            entity = engine._require_entity(entity_id)
            peers = _peers(engine, entity)
            if not peers:
                return _result({"text": "这里除了你空无一人。", "entities": []})
            lines = ["这里还有："]
            for e in peers:
                lines.append(f"- {e.name}（{e.kind}）：{e.desc or '无描述'}")
            return _result(
                {"text": "\n".join(lines), "entities": [e.to_dict() for e in peers]}
            )
        except (WorldError, McpAuthError) as e:
            return _result({"text": str(e)})

    # ---------- 动作（world_move / world_say / world_bag / world_use / world_interact） ----------

    @mcp.tool()
    async def world_move(ctx: Context, direction: str, path: int | None = None) -> str:
        """沿方向移动到新位置，返回新场景。

        Args:
            direction: up/right/down/left（world_look 返回的可移动方向）。
            path: 可选，该方向多条平行路径时的路径索引。
        """
        try:
            entity_id = _entity_id(ctx, fixed_identity)
            scene = await engine.move(entity_id, direction, path=path)
            peers = _peers(engine, engine._require_entity(entity_id))
            return _result(
                {
                    "text": _scene_text(scene, peers),
                    "scene": scene.to_dict(),
                    "entities": [e.to_dict() for e in peers],
                }
            )
        except (WorldError, McpAuthError) as e:
            return _result({"text": f"移动失败：{e}"})

    @mcp.tool()
    async def world_say(ctx: Context, text: str, scope: str = "cell") -> str:
        """说话。scope=cell 对同地块说话（不限）；scope=world 全图广播
        （消耗 1 个喇叭 + 每人 30 秒冷却）。

        Args:
            text: 说话内容。
            scope: cell（默认）或 world。
        """
        try:
            entity_id = _entity_id(ctx, fixed_identity)
            await engine.say(entity_id, text, scope=scope)
            return _result({"text": f"你说：「{text}」"})
        except (WorldError, McpAuthError) as e:
            return _result({"text": f"说话失败：{e}"})

    @mcp.tool()
    async def world_bag(ctx: Context) -> str:
        """查看你的背包（物品 / 数量 / 个体属性）。"""
        try:
            entity_id = _entity_id(ctx, fixed_identity)
            items = engine.list_inventory(entity_id)
            if not items:
                return _result({"text": "你的背包空空如也。", "items": []})
            lines = ["你的背包："]
            for item in items:
                name = item["def"]["name"] if item["def"] else item["item_id"]
                lines.append(f"- {name} × {item['count']}")
            return _result({"text": "\n".join(lines), "items": items})
        except (WorldError, McpAuthError) as e:
            return _result({"text": str(e)})

    @mcp.tool()
    async def world_use(ctx: Context, item_id: str, args: dict | None = None) -> str:
        """使用背包中的一件物品（触发其 use 交互，如吃苹果）。

        Args:
            item_id: 物品 id（world_bag 返回的 item_id）。
            args: 可选，传给 use 交互的参数。
        """
        try:
            entity_id = _entity_id(ctx, fixed_identity)
            item = engine.store.items.get(item_id)
            if item is None:
                return _result({"text": f"没有这种物品：{item_id}"})
            if not item.use_action:
                return _result({"text": f"「{item.name}」不能直接使用"})
            result = await engine.interact(
                entity_id, entity_id, item.use_action, args=args, item_id=item_id
            )
            result.ui = await engine.apply_ui_hooks(result.ui)
            return _result(result.to_dict())
        except (WorldError, McpAuthError) as e:
            return _result({"text": str(e)})

    @mcp.tool()
    async def world_interact(
        ctx: Context, target_id: str, action: str, args: dict | None = None
    ) -> str:
        """与世界中的实体交互（对话 / 交易 / 开门等）。

        Args:
            target_id: 目标实体 id（world_look / world_who 返回）。
            action: 动作名（world_look 的实体可用动作）。
            args: 可选，交互参数。
        """
        try:
            entity_id = _entity_id(ctx, fixed_identity)
            result = await engine.interact(entity_id, target_id, action, args=args)
            result.ui = await engine.apply_ui_hooks(result.ui)
            return _result(result.to_dict())
        except (WorldError, McpAuthError) as e:
            return _result({"text": str(e)})

    return mcp


def _peers(engine: Any, entity: Any) -> list:
    """同地块的其他实体（不含自己）。"""
    return [
        e
        for e in engine.list_entities(entity.map_id, entity.row, entity.col)
        if e.id != entity.id
    ]


def _scene_text(scene: Any, peers: list) -> str:
    """场景 → 中文文本（LLM 消费；路径 + 实体行）。"""
    lines = [
        f"你当前位于：{scene.location.name}",
        f"描述：{scene.description}",
    ]
    if scene.paths:
        lines.append("可移动的方向：")
        for p in scene.paths:
            target = p.target_name if p.target_name else "???"
            label = f"{p.label} → {target}" if p.label else f"→ {target}"
            lines.append(f"  {p.direction}[{p.path_index}] {label}")
    else:
        lines.append("这里没有任何可走的路。")
    if peers:
        lines.append("同地块的实体：")
        for e in peers:
            lines.append(f"  - {e.name}（{e.kind}）")
    return "\n".join(lines)
