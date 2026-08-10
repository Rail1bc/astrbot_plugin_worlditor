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

- `Location(id, name, description, layout_x=None, layout_y=None)` — `layout` 仅可视化提示，与拓扑无关。
- `Exit(id, from_id, to_id, label, reveal_target=True)` — 有向；同 `(from_id, to_id)` 允许多条不同 label 的出边；`reveal_target=False` 时场景隐藏目标名（显示 `???`）。
- `Player(player_id, name, location_id, is_agent=False, last_active_ts=0.0, user_id=None)` — 人类（v1）仅内存；agent 固定 `player_id="agent"` 位置持久化；`user_id` 为 v2 用户系统预留。

"迷路"效果由图本身实现：多边同目标、隐藏目标、环路。

## 引擎动作（world/engine.py，协议无关）

全部变更在实例 `asyncio.Lock` 内；读路径走内存快照、免锁。

- `await engine.describe_scene(player_id) -> SceneView | None`
- `await engine.move(player_id, exit_id) -> SceneView` — 校验顺序：玩家存在 → 出口存在 → 出口属于当前地块 → 移动 → agent 写回 SQLite；失败抛 `WorldError`（消息可直接展示）。
- `await engine.register_player(player_id, name=None, *, is_agent=False, user_id=None) -> Location`（幂等）
- `await engine.deregister_player(player_id) -> bool`（agent 不可注销）
- `await engine.touch(player_id) -> bool`（刷新活跃时间）
- 只读：`list_locations()` / `list_all_exits()` / `list_exits(location_id)` / `get_location(id)` / `get_exit(id)` / `get_player(id)` / `list_players()`
- 后台任务：`initialize()` 启动、`terminate()` 取消；每 60s 清理超 15 分钟无活动的非 agent 玩家。

`scene_to_text(scene) -> str`：场景渲染为中文文本（LLM 工具注入下一轮 prompt 的形态）。

## 持久化（world/store.py）

数据库：`data/plugin_data/astrbot_plugin_worlditor/world.db`

| 表 | 说明 |
|---|---|
| `locations(id TEXT PK, name, description, layout_json)` | 地块；`layout_json` 存 `{"x","y"}` 坐标提示 |
| `exits(id TEXT PK, from_id, to_id, label, reveal_target)` | 有向出口；from_id/to_id 外键 locations，同 (from,to) 允许多行 |
| `world_meta(key TEXT PK, value)` | `schema_version` + `agent_location` |

- WAL + `foreign_keys=ON`；启动全量载入内存（读路径快）。
- `locations` 为空时幂等播种示例小镇（小镇区 + 迷雾区：多边同目标 / 隐藏目标 / 环路），agent 初始在广场。

## LLM 工具（main.py）

- `world_look` — 当前地块（id/名称/描述）+ 出边列表；隐藏目标显示 `???`。
- `world_move(exit_id)` — docstring 必须 `exit_id(string): ...`（参数缺类型注解 import 即 ValueError）；校验失败返回中文错误串让 LLM 自纠，不抛异常。

## Web API（api/，插件页）

经 `context.register_web_api(f"/{PLUGIN_NAME}{path}", ...)` 注册，`/api/plug/astrbot_plugin_worlditor/...` 分发：

| 端点 | 说明 |
|---|---|
| `GET /world/state?player_id=...` | 全量地图（locations + exits）+ 该玩家场景 + agent 位置 |
| `POST /world/player/register` `{name?}` | 随机 player_id（uuid4 前 8 位）、默认名 `旅行者-XXXX`、放起始地块；返回 `{player_id, location_id, location_name}` |
| `POST /world/move` `{player_id, exit_id}` | 按出口移动并返回新场景；非法出边 → 400 |
| `POST /world/player/deregister` `{player_id}` | 页面 unload 尽力注销（超时清理兜底） |

## 插件网页（pages/world/）— v1 调试工具

定位：供管理员在 dashboard 内验证世界与移动逻辑，非正式用户入口（正式入口为 v2 独立网页）。

- 无本地 player_id → 先注册 → `GET /world/state` → 渲染。
- SVG 有向图：节点 + 有向箭头 + label；`layout` 坐标优先，未设坐标用确定性兜底布局；当前地块高亮 + agent 静态标记。
- 出边按钮列表（label + 目标名或 `???`），按 exit_id 移动。
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

有向图编辑：增删地块、增删带标签有向出边、设置 `reveal_target` / 布局坐标。

### 人与 agent 实时互见（SSE 事件流）

全量快照广播（发布时刻拷贝，防共享引用漂移）；`stream_response` + `subscribeSSE`。

### MCP 封装（预留）

AstrBot 无 MCP 服务端；LLM 工具与 MCP 工具收敛为同一 FunctionTool，对 LLM 完全等价。预留 MCP = action 层协议无关；后续把 WorldEngine 抽成独立进程 + FastMCP 薄封装，工具调用路径零改动。

### v3：独立应用

独立仓库的方向预留（移动客户端 / 更完整形态），最后考虑。
