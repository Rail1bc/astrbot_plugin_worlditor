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

# ---------- v4.1 REST 非动作端点（B10：无动作端点，动作统一走 MCP） ----------

_V4_ROUTES: tuple[tuple[str, str, list[str], str], ...] = (
    # 身份注册与凭据（B13）
    (
        "/world/v4/register",
        "world_v4_register",
        ["POST"],
        "人类自助注册（用户名+密码 → player 实体 + play 档凭据）",
    ),
    (
        "/world/v4/login",
        "world_v4_login",
        ["POST"],
        "人类登录（旧凭据吊销，刷新新凭据）",
    ),
    (
        "/world/v4/register-agent",
        "world_v4_register_agent",
        ["POST"],
        "agent 自助注册（→ agent 实体 + play 档凭据）",
    ),
    (
        "/world/v4/logout",
        "world_v4_logout",
        ["POST"],
        "注销当前凭据",
    ),
    (
        "/world/v4/change-password",
        "world_v4_change_password",
        ["POST"],
        "玩家自助改密（改后旧凭据全部失效）",
    ),
    (
        "/world/v4/revoke",
        "world_v4_revoke",
        ["POST"],
        "管理员吊销一份凭据",
    ),
    (
        "/world/v4/read-token",
        "world_v4_read_token",
        ["GET"],
        "开放模式：获取 read 档围观凭据（免注册）",
    ),
    (
        "/world/v4/invite-codes",
        "world_v4_create_invite_codes",
        ["POST"],
        "管理员批量生成邀请码",
    ),
    (
        "/world/v4/invite-codes",
        "world_v4_list_invite_codes",
        ["GET"],
        "管理员查看邀请码列表",
    ),
    # 只读快照（read+）
    (
        "/world/v4/state",
        "world_v4_state",
        ["GET"],
        "世界全量快照：maps / locations / entities",
    ),
    (
        "/world/v4/scene",
        "world_v4_scene",
        ["GET"],
        "实体场景（read 档围观任意；play 档缺省自己）",
    ),
    (
        "/world/v4/bag",
        "world_v4_bag",
        ["GET"],
        "实体背包（play 档自己；admin 可指定 entity_id）",
    ),
    # SSE 事件流（play+，B11）
    (
        "/world/v4/events",
        "world_v4_events",
        ["GET"],
        "SSE 事件流（事件总线序列化出口，EventSource 消费）",
    ),
    # 玩法包 web 静态资源（read+，B9）
    (
        "/world/v4/plays/<play_id>/web/<path:path>",
        "world_v4_play_web",
        ["GET"],
        "玩法包 web/ 静态资源（自定义界面组件）",
    ),
    # admin 管理端点（admin 档）
    (
        "/world/v4/admin/location/create",
        "world_v4_admin_location_create",
        ["POST"],
        "新建地块",
    ),
    (
        "/world/v4/admin/location/update",
        "world_v4_admin_location_update",
        ["POST"],
        "更新地块名称 / 描述",
    ),
    (
        "/world/v4/admin/location/delete",
        "world_v4_admin_location_delete",
        ["POST"],
        "删除地块（级联删实体与引用）",
    ),
    (
        "/world/v4/admin/location/move",
        "world_v4_admin_location_move",
        ["POST"],
        "移动地块",
    ),
    (
        "/world/v4/admin/connection/update",
        "world_v4_admin_connection_update",
        ["POST"],
        "更新地块某方向连接槽位",
    ),
    (
        "/world/v4/admin/map/create",
        "world_v4_admin_map_create",
        ["POST"],
        "新建地图（多图支持）",
    ),
    (
        "/world/v4/admin/map/update",
        "world_v4_admin_map_update",
        ["POST"],
        "更新地图属性",
    ),
    (
        "/world/v4/admin/entity/place",
        "world_v4_admin_entity_place",
        ["POST"],
        "放置实体（B8：实体 = 地图编辑内容）",
    ),
    (
        "/world/v4/admin/entity/remove",
        "world_v4_admin_entity_remove",
        ["POST"],
        "移除实体",
    ),
    (
        "/world/v4/admin/entity/update",
        "world_v4_admin_entity_update",
        ["POST"],
        "更新实体（name/desc/attrs/state）",
    ),
)
