# 世界编辑器（astrbot_plugin_worlditor）设计文档

## 定位

一个可以无限生长的世界：图结构为底座，实体与交互为核心内容，AI 与人都能进入。

- **世界 = 有向图**：地块（Location）为节点，带标签的出口（Exit）为有向边。`a→b` 可达**不蕴含** `b→a`；无空间相邻，只有出边构成"相邻/可达"。
- **实体与交互是内容**（长期愿景）：实体横跨人物、生物与非生物——建筑、路障，乃至非现实物品；交互方式可无限扩展，交互总有新花样。
- **引擎协议无关**：动作层被 LLM 工具 / 插件页 API / 未来世界 HTTP API 共用，为 MCP 封装预留。
- **v1 现状**：基础有向图地图 + 移动；人类玩家为隐形实体（仅内存），agent 有 `world_look` / `world_move` 工具且位置跨对话持久化；框架内置插件页仅作调试。

## 架构

```
LLM 工具 / 插件页 API / (未来) 世界 HTTP API
        │  同一套协议无关动作
        ▼
WorldEngine（world/engine.py，实例锁内变更）
        │
        ▼
WorldStore（world/store.py，SQLite aiosqlite + WAL，启动全量载入内存）
```

| 目录 | 职责 |
|---|---|
| `world/` | 世界引擎（纯 Python，协议无关，可被未来 MCP / 世界 HTTP API 复用） |
| `api/` | Web API handler mixin（插件页用） |
| `pages/world/` | 框架内置调试页（纯 ES module，无构建） |
| `main.py` | Star 装配：引擎 + 路由注册 + LLM 工具 |
| `tests/` | 引擎 / API 单测 |

## 数据模型（world/model.py）

- `Location(id, name, description, layout_x=None, layout_y=None)` — `layout` 为编辑表格的整数网格坐标（x=列、y=行，决定地块主位），仅可视化提示、与拓扑无关（可达性只由出边定义）；未设坐标时编辑器确定性兜底到首个空闲格。
- `Exit(id, from_id, to_id, label, reveal_target=True, direction="up")` — 有向；同 `(from_id, to_id)` 允许多条不同 label 的出边；`reveal_target=False` 时场景隐藏目标名（显示 `???`）；`direction` 为玩家视图十字槽位方向（`up/right/down/left`），编辑器保证同一出发地块的出边方向互异（数据层不强制）。
- `Player(player_id, name, location_id, is_agent=False, last_active_ts=0.0, user_id=None)` — 人类（v1）仅内存；agent 固定 `player_id="agent"` 位置持久化；`user_id` 为 v2 用户系统预留。

"迷路"效果由图本身实现：多边同目标、隐藏目标、环路。**约束分层**：数据层不设出度限制（合法 = 结构允许），规范由可视化编辑器保证，视图层以「+N」折叠兜底违规地图。

## 引擎动作（world/engine.py，协议无关）

全部变更在实例 `asyncio.Lock` 内；读路径走内存快照、免锁。

- `await engine.describe_scene(player_id) -> SceneView | None`
- `await engine.move(player_id, exit_id) -> SceneView` — 校验顺序：玩家存在 → 出口存在 → 出口属于当前地块 → 移动 → agent 写回 SQLite；失败抛 `WorldError`（消息可直接展示）。
- `await engine.register_player(player_id, name=None, *, is_agent=False, user_id=None) -> Location`（幂等）
- `await engine.deregister_player(player_id) -> bool`（agent 不可注销）
- `await engine.touch(player_id) -> bool`（刷新活跃时间）
- 地图编辑（v0.2，全部锁内；`_UNSET = object()` 哨兵区分「参数未提供=不变」与「显式传 None=清空/重置」）：
  - `await engine.create_location(id, name, description="", *, layout_x=None, layout_y=None) -> Location`
  - `await engine.update_location(id, *, name=_UNSET, description=_UNSET, layout_x=_UNSET, layout_y=_UNSET) -> Location`
  - `await engine.delete_location(id) -> None` — 级联删出边；拒绝删除有玩家（含 agent）所在地块
  - `await engine.create_exit(id, from_id, to_id, label, *, reveal_target=True, direction="up") -> Exit`
  - `await engine.update_exit(id, *, to_id=_UNSET, label=_UNSET, reveal_target=_UNSET, direction=_UNSET) -> Exit`（from_id 不可变）
  - `await engine.delete_exit(id) -> None`
  - 校验（结构合法，不设出度限制）：id/name/label 去空格非空、重复 id 报错、from/to 必须存在、direction 在 `DIRECTIONS` 内、layout 拒绝非数字 / bool / NaN / Inf；自环出口合法
- 只读：`list_locations()` / `list_all_exits()` / `list_exits(location_id)` / `get_location(id)` / `get_exit(id)` / `get_player(id)` / `list_players()`
- 后台任务：`initialize()` 启动、`terminate()` 取消；每 60s 清理超 15 分钟无活动的非 agent 玩家。

`scene_to_text(scene) -> str`：场景渲染为中文文本（LLM 工具注入下一轮 prompt 的形态）。

## 持久化（world/store.py）

数据库：`data/plugin_data/astrbot_plugin_worlditor/world.db`

| 表 | 说明 |
|---|---|
| `locations(id TEXT PK, name, description, layout_json)` | 地块；`layout_json` 存 `{"x","y"}` 整数网格坐标（编辑表格主位） |
| `exits(id TEXT PK, from_id, to_id, label, reveal_target, direction)` | 有向出口；from_id/to_id 外键 locations，同 (from,to) 允许多行；direction 为十字槽位方向 |
| `world_meta(key TEXT PK, value)` | `schema_version` + `agent_location` |

- WAL + `foreign_keys=ON`；启动全量载入内存（读路径快）。
- `locations` 为空时幂等播种示例小镇（小镇区 + 迷雾区：多边同目标 / 隐藏目标 / 环路），agent 初始在广场。
- 版本迁移（schema v2）：`_migrate()` 用 `PRAGMA table_info(exits)` 检查 `direction` 列，老库缺列时 `ALTER TABLE exits ADD COLUMN direction TEXT NOT NULL DEFAULT 'up'`。
- 编辑写操作（`save_location` / `delete_location_with_exits` / `save_exit` / `delete_exit`）遵循 DB 先、内存后的约定，同时维护 `exits_by_from` 索引（空桶删 key）。

## LLM 工具（main.py）

- `world_look` — 当前地块（id/名称/描述）+ 出边列表；隐藏目标显示 `???`。
- `world_move(exit_id)` — docstring 必须 `exit_id(string): ...`（参数缺类型注解 import 即 ValueError）；校验失败返回中文错误串让 LLM 自纠，不抛异常。

## Web API（api/，插件页）

经 `context.register_web_api(f"/{PLUGIN_NAME}{path}", ...)` 注册，`/api/plug/astrbot_plugin_worlditor/...` 分发：

| 端点 | 说明 |
|---|---|
| `GET /world/state?player_id=...` | 全量地图（locations + exits）+ 该玩家场景 + agent 位置 + 出生点（agent/玩家注册起始，默认播种位、被删则回落第一个地块） |
| `POST /world/player/register` `{name?}` | 随机 player_id（uuid4 前 8 位）、默认名 `旅行者-XXXX`、放起始地块；返回 `{player_id, location_id, location_name}` |
| `POST /world/move` `{player_id, exit_id}` | 按出口移动并返回新场景；非法出边 → 400 |
| `POST /world/player/deregister` `{player_id}` | 页面 unload 尽力注销（超时清理兜底） |
| `POST /world/location/create` `{id, name, description?, layout?}` | 新建地块；`layout` 为 `{x, y}` 或 null |
| `POST /world/location/update` `{id, name?, description?, layout?}` | 更新地块；缺省键不变，`layout: null` = 清空坐标 |
| `POST /world/location/delete` `{id}` | 删除地块（级联删出边，拒绝删除有玩家占据的地块） |
| `POST /world/exit/create` `{id, from_id, to_id, label, reveal_target?, direction?}` | 新建出口 |
| `POST /world/exit/update` `{id, to_id?, label?, reveal_target?, direction?}` | 更新出口（from_id 不可变） |
| `POST /world/exit/delete` `{id}` | 删除一条出口 |

handler 只做类型校验（dict、字符串、layout 数字排除 bool、reveal_target 布尔、direction 字符串），语义校验抛给引擎（`WorldError` → 400 error 信封）；update 按 payload 出现的键拼 kwargs。

## 插件网页（pages/world/）— 单页双模式调试工具

定位：供管理员在 dashboard 内验证世界与移动逻辑，非正式用户入口（正式入口为 v2 独立网页）。单页内分段控件切换两种视图（`app.js` / `shared.js` / `edit-view.js` / `edit-forms.js` / `play-view.js`，纯 ES module 无构建）。

- 无本地 player_id → 先注册 → `GET /world/state` → 渲染。
- **编辑模式（上帝视角，网格表格）**：地块按其整数网格坐标（`layout`：列/行）落在 CSS grid 表格的主格，**所有连接必须相邻**——出口方向决定目标相邻格，目标主位不在该格时在该格显示目标的地块分身（虚线框，可叠多行）；反向边用相反方向（A 右是 B ⇔ B 左是 A），捷径 / 环路这类特殊关系以分身呈现（如 悬崖-单向可达-悬崖底部：底部→悬崖 用反向方向、悬崖以分身出现在底部的相邻格）；分身可收起（折叠成出发地块格内的标签，点击展开；工具栏一键全收/全展）；格上显示出生点徽标（agent / 玩家注册起始）；地块永远真名、不存在 `???`；格间空隙内画方向标签（↑↓←→ + 出口 label，隐藏目标仍显示真名）；重名地块悬浮全体高亮（识别重名）；点击主格 → 地块表单（id/name/description/列/行），点击分身 → 出口表单（from/to/label/「隐藏目的地」开关/direction 四选/删除+确认），新建出口方向自动推荐（① 反向边存在 → 其反方向；② 目标相邻 → 相对位置推导；③ 否则首个空闲方向），提交后 re-fetch 世界状态重绘。
- **玩家模式**：地图 div 中间是**只含地块名称的小块**（无说明文本 / 玩家 id），有出边连接的 1 跳目标按 `direction` 放上/右/下/左——**目标格只显示地块名**（无出口标签等详细信息；格收缩到内容大小并居中，长名在格内换行、不再省略号截断）；**无连接的槽位不可见**；所有边一视同仁（无箭头简单连线，不查反向边、不画方向）；**无回环**（自环出口照常占槽位、目标格即当前地块）；隐藏目标显示 `???`（名称只取 scene 的 `target_name`，绝不全图查名）；当前地块说明文本渲染在与地图 div **平级的独立信息 div**（实体系统后续版本接入）；**视口内整体不滚动**：页面 `body` 固定 `100dvh` 高度，地图 div **动态填充剩余空间**（棋盘正方形取地图区域宽/高较小值、随区域变化自适应），详情列在 PC 为右侧一整列、移动端为下方固定高度区，均可单独滚动；违规地图（出度 >4 或同方向冲突）前 4 槽 + 「+N」折叠 → 展开全部出口列表（保留 exit_id）可收回；点击目标格按 exit_id 移动。
- 无 SSE；sandbox iframe 无 localStorage / 原生 alert → 自绘 modal + textContent 转义 + 模块级 playerId（刷新重新注册）。

## 一致性

- 全部变更在实例锁内（`asyncio.Lock` 不可重入：public 动作不互相调用）。
- aiosqlite 真异步（连接内语句排队），锁内直接 `await`，无需 to_thread。
- 人类移动只改内存；agent 移动额外写回 SQLite（跨对话连续）。

## 后续计划

### v2：独立网页（正式用户入口）

- 同项目内容（同一世界），端口 **8111**，**移动端优先**（移动端可能是主要平台）。
- 参考：livingmemory 旧版独立 WebUI（FastAPI + uvicorn 进程内托管）/ self_learning（Quart + Hypercorn 守护线程）。
- **用户系统**：注册账户（密码哈希存储）→ 登录 → token；世界玩家与账户绑定（`Player.user_id` 已预留），替代 v1 随机隐形实体作为正式身份。
- **插件 = 唯一权威后端**：暴露世界 HTTP API（`/api/world/*`，包装同一 WorldEngine）+ 共享 token；配置项 `enable_world_api` / `world_api_token` / `allowed_origins`。
- **CORS**：`/api/plug` 不设 CORS 头；外部调用路由前缀与插件页 cookie 会话路由隔离，共享 token 只挂外部前缀。
- 引擎 action 签名保持纯参数（不接收 request/token），鉴权只在 API handler 薄层。

### 实体与交互系统（v2/v3 核心扩展）

- 实体（Entity）：人物、生物、建筑、路障、非现实物品……皆为实体。
- 交互（Interaction）：对话 / 开启 / 破坏 / 阅读……交互方式可无限扩展。
- 预留形态：`world_interact(entity_id, interaction)` 类工具；LLM 生成 NPC 对话。

### 地图可视化编辑

有向图编辑：增删地块、增删带标签有向出边、设置 `reveal_target` / 网格坐标。v0.2 已在插件调试页落地单页双模式的编辑能力（网格表格 + 表单编辑、方向自动推荐、地块分身可收起、重名高亮）；v2 独立网页将作为正式的可视化编辑入口。

### 人与 agent 实时互见（SSE 事件流）

全量快照广播（发布时刻拷贝，防共享引用漂移）；`stream_response` + `subscribeSSE`。

### MCP 封装（预留）

AstrBot 无 MCP 服务端；LLM 工具与 MCP 工具收敛为同一 FunctionTool，对 LLM 完全等价。预留 MCP = action 层协议无关；后续把 WorldEngine 抽成独立进程 + FastMCP 薄封装，工具调用路径零改动。

### v3：独立应用

独立仓库的方向预留（移动客户端 / 更完整形态），最后考虑。
