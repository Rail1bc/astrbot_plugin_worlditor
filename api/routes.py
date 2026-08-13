"""Web API 路由表：一处看全，新增端点只需加一行。

handler 用方法名字符串 + getattr 解析（main.py 的 Star 注册时），构造期即
校验拼写（拼错直接抛 AttributeError）。
"""

_ROUTES: tuple[tuple[str, str, list[str], str], ...] = (
    (
        "/world/state",
        "world_state",
        ["GET"],
        "查看世界全量地图（maps + locations + templates）与指定玩家的场景",
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
        "按方向移动玩家（多路径时带 path 索引），返回新场景",
    ),
    (
        "/world/player/deregister",
        "world_deregister",
        ["POST"],
        "注销玩家（页面 unload 尽力调用，超时清理兜底）",
    ),
    (
        "/world/location/create",
        "world_location_create",
        ["POST"],
        "新建地块（row/col/name 必填，可指定 template_id 以模板为蓝本）",
    ),
    (
        "/world/location/update",
        "world_location_update",
        ["POST"],
        "更新地块名称 / 描述（坐标只读）",
    ),
    (
        "/world/location/delete",
        "world_location_delete",
        ["POST"],
        "删除地块（级联清除指向它的目标，拒绝删除有玩家占据的地块）",
    ),
    (
        "/world/location/move",
        "world_location_move",
        ["POST"],
        "移动地块（原子重写全图引用与玩家位置，目标格被占则拒绝）",
    ),
    (
        "/world/connection/update",
        "world_connection_update",
        ["POST"],
        "更新地块某方向连接槽位（enabled 与 paths 整体替换）",
    ),
    (
        "/world/template/create",
        "world_template_create",
        ["POST"],
        "从源地块捕获地块模板（同图目标存相对偏移）",
    ),
    (
        "/world/template/update",
        "world_template_update",
        ["POST"],
        "更新模板名称或重新捕获源地块",
    ),
    (
        "/world/template/delete",
        "world_template_delete",
        ["POST"],
        "删除模板",
    ),
    (
        "/world/template/apply",
        "world_template_apply",
        ["POST"],
        "应用模板到空地块（同图目标平移，跨图目标原样复制）",
    ),
)
