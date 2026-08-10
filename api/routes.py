"""Web API 路由表：一处看全，新增端点只需加一行。

handler 用方法名字符串 + getattr 解析（main.py 的 Star 注册时），构造期即
校验拼写（拼错直接抛 AttributeError）。
"""

_ROUTES: tuple[tuple[str, str, list[str], str], ...] = (
    (
        "/world/state",
        "world_state",
        ["GET"],
        "查看世界全量地图（locations + exits）与指定玩家的场景",
    ),
    (
        "/world/player/register",
        "world_register",
        ["POST"],
        "注册一个隐形玩家，返回随机 player_id（仅内存，超时清理）",
    ),
    (
        "/world/move",
        "world_move",
        ["POST"],
        "按出口 id 移动玩家（多边同目标 / 隐藏目标下 exit_id 才是唯一语义）",
    ),
    (
        "/world/player/deregister",
        "world_deregister",
        ["POST"],
        "注销玩家（页面 unload 尽力调用，超时清理兜底）",
    ),
)
