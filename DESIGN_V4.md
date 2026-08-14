# 世界底子 v4 设计文档（astrbot_plugin_worlditor）

> 状态：设计定稿（2026-08，A/B/C 组决策已全部确认，待实施）。
> 配套：DESIGN.md（v3 现状）。v4 为破坏性重构，**无迁移**：旧 world.db 直接丢弃，空库按 v4 重建播种。

## 定位转变：从「世界编辑器」到「世界底子」

worlditor 从"一个探索世界"升级为**世界平台**：

- **内核（worlditor）= 世界的事实模型 + 动作原语 + 注册表 + 事件总线 + MCP 服务 + 基础 UI**，明确不做任何具体玩法。
- **玩法（worlditor_play_\* 玩法包）= 数据 + 代码外挂**，通过注册表接入内核，从开源社区生长：角色养成、物品合成/升级、交易市场、战斗系统等均由玩法包实现。

四条铁律：

1. **内核只懂原语**：移动、说话、拾取、给/扣物品、触发交互、改状态、广播事件。不含伤害公式、合成配方、价格、升级曲线。
2. **玩法即数据**：物品、对话树、配方、商店货单都是玩法包自带的数据文件；行为逻辑（战斗计算、实体 AI、经济）是玩法包代码。
3. **扩展靠注册表，不靠改内核**：新增实体种类 / 交互动作 / 物品用途 / 事件订阅 / UI 界面，全部走注册 API。
4. **UI 是协议**：交互界面由内核按 `UiBlock` schema 渲染，玩法包只描述、不画界面；**玩法包可注册自定义界面组件与界面扩展**（B9）——向已有界面添加内容或重写渲染；WebUI 与 MCP 工具共用同一份交互结果。

## 决策记录（2026-08 逐项确认）

### 形态决策

| 决策点 | 结论 |
|---|---|
| 玩法包形态 | worlditor 自动发现加载 `worlditor_play_*`，独立仓库、不占 AstrBot 插件位；版本自声明（`requires`） |
| 独立 WebUI 技术栈 | Vue 3 + Vite，移动端优先，独立部署（独立端口 / 静态托管），token 鉴权 |
| v3 → v4 迁移 | **无迁移，空库重建**（延续 v3 先例）：旧 world.db 直接丢弃，空库重建播种（含演示实体与物品） |
| 版本号 | 统一为 v4.0.0（metadata.yaml / README / CHANGELOG 同步，修复 v3 版本漂移） |

### A 组（契约语义）

| # | 决策点 | 结论 |
|---|---|---|
| A1 | 交互双轨 | **保留双轨**：交互 = 声明式 `effects`（内核结算，结果可展示给 UI）；事件/tick = 命令式原语（直接 `api.give_item()` 等，无展示需求）。这是有意设计 |
| A2 | IM 侧游玩 | **人类玩家明确走独立 WebUI，IM 侧不游玩**（v4.0 不做群聊游玩命令；管理员调试命令可选）。交互结果渲染目标 = WebUI（及 MCP 工具返回） |
| A3 | on_tick 调度 | 内核单循环按最小间隔跑，各 handler 记录上次执行时间（各自间隔）；**串行执行 + 异常隔离，世界动作不排队等玩法** |
| A4 | 实体行为脚本 | **内核不做行为脚本**：v4.0 实体为静态（含可交互实体）；实体移动/行为逻辑全部由玩法包 `on_tick` 实现 |
| A5 | 统一身份 | **封装带身份验证的 MCP 服务**：所有 agent 接入与玩家接入都需验证身份；agent 与玩家没有本质区别——玩家经 WebUI 点击操作代表自己的实体，agent 经 MCP 调用工具行动（详见「MCP 服务与统一身份」） |

### B 组（细节）

| # | 决策点 | 结论 |
|---|---|---|
| B1 | 实体显示 | 仅需实体名称 + **kind 类型标签**（简单样式区分：玩家/AI/商贩/物品…），不做图标资源管线（`icon` 字段可选、后议）；实体列表保持简单，实体可点击交互；弹窗按实体类型可扩展——简单实体 = 文本/表单/按钮弹窗，人物型实体（含玩家） = **角色卡**（头像 + 属性等内容，UiBlock 新增 `character` kind） |
| B2 | 全图广播限流 | **道具形式限制**：内置广播道具（喇叭），`say(scope=world)` 消耗 1 个 + 道具级冷却（每人冷却 30s，管理员豁免）；cell 级不限制。喇叭获取途径由玩法包提供（商人出售/任务奖励） |
| B3 | 日志容量 | `world_log` 上限 **5000 条**，写入时触发清理（按 id 删最旧） |
| B4 | WebUI 鉴权 | token 三档：`read`（围观）/ `play`（移动/交互/说话）/ `admin`（编辑/管理） |
| B5 | id 格式 | **实体 id 一律 uuid4 hex**（防猜测/碰撞）；**物品 id 为类型键**（玩法包注册时提供短标识如 `apple`，同 id 覆盖更新；不提供则 uuid4 hex）；`user_id` 字段才承载实例/账户信息（联邦预留） |
| B6 | demo 位置 | 内核自带 `demo_play/`（随发行、可删）+ 扫描**worlditor 数据目录下 `plays/`**（`data/plugin_data/astrbot_plugin_worlditor/plays/`，经 `StarTools.get_data_dir` 获取）中的 `worlditor_play_*` 社区玩法包——**不放在框架插件目录 `data/plugins/`**，避免被 AstrBot 插件系统扫描识别产生不可预料影响 |
| B7 | MCP 形态 | **进程内 MCP server**：FastMCP 薄封装引擎动作，**同时暴露 streamable HTTP（公网远程接入）与 stdio（本地）两种传输**。实现：streamable HTTP = FastMCP `streamable_http_app()` + 认证中间件，由插件**独立 uvicorn 服务**承载（配置 `enable_world_api` / `world_api_host` / `world_api_port`，默认 6288，HTTPS 反向代理指向它；不挂 AstrBot HTTP 服务，避免依赖其内部 ASGI 挂载点）；stdio = 独立进程入口（`python -m astrbot_plugin_worlditor.world.mcp.stdio`，`--db`/`--token` 或环境变量），一个连接绑定一个实体。连接即身份验证（token → 实体，kind=agent/player），已落地。**MCP 即联邦通道**：未安装 worlditor 的远程 AstrBot（乃至任意 MCP 客户端）经 MCP client 配置即可加入世界（见「联邦（v5）」） |
| B8 | 实体定位 | **实体 = 地图编辑内容**：由地图编辑器直接放置到地块上（admin 权限，与地块/连接/模板同级），删除地块时级联删除其上实体；**玩法包扩展的是实体的类型（kind）与行为**（交互动作 / tick 状态机：巡逻、开合、复活），**不提供实体生成/移除原语**（实体存在性由地图内容决定；`move_entity` 保留供行为驱动） |
| B9 | 界面扩展 | **玩法包可自定义交互界面，或向已有界面添加/重写内容**：① `UiBlock` 新增 `custom` kind（引用玩法包注册的 Web Component，带 `fallback_text` 供 MCP 侧降级）；② `register_ui_hook` 向已有界面块注入子块，`position ∈ {"before", "after", "replace"}`——before/after 添加内容，**replace 重写渲染**（返回 custom 块即完整自定义界面，与独立渲染器 API 功能等价，故不设 `register_ui_renderer`）。组件代码与玩法包同信任级别，经身份化 bridge 调世界 API。v4.1 WebUI 落地 |
| B10 | 动作通道唯一 | **MCP 是唯一动作通道，WebUI 也是 MCP 客户端**：人类玩家与 agent 共用同一套工具（移动/交互/物品/说话）、同一套身份认证（token → 实体，kind=player/agent）、同一份返回协议（结构化 JSON：`text` 供 LLM、`ui` 供渲染）；**REST 仅保留非动作**：只读状态快照（地图/场景/背包）、SSE 事件流、web 静态资源、admin 管理端点。v4.0 不暴露 HTTP 动作端点（调试页走进程内 Python 动作），对外动作通道统一在 v4.1 由 MCP 承担 |
| B11 | 实时性分工 | **动作走 MCP（请求-响应），实时感知走 SSE（推送），不轮询 MCP**：WebUI 以浏览器原生 EventSource 订阅事件流（play 档 token），事件驱动**增量更新**（角色位置/说话/交互/实体状态），无法增量表达的事件（如地图被编辑）触发一次状态快照拉取兜底；断线重连后先拉快照补齐遗漏。**MCP notifications 不用于业务事件**（面向协议级变化，避免双轨） |
| B12 | 概念收敛 | **世界只有两个概念：地块与实体。取消 NPC/角色独立概念**——玩家是内置实体类型 `kind="player"`（人类控制），AI agent 是内置类型 `kind="agent"`（MCP 控制），NPC 只是玩法包注册的实体 kind（"饮料商人"与"自动售卖机"在功能/表现上无本质区别，差异仅是 kind 与文本）；身份化实体（player/agent）可被认证绑定、有背包、位置持久化；所有实体统一 `Entity` 模型与 `entities` 表，统一交互/事件/原语 |
| B13 | 自助注册 | **人类玩家与 agent 均自助注册获取凭据，不依赖管理员人工签发**：三种世界注册模式（配置）——**开放**（任何人注册，限流防滥用）/ **邀请码**（需世界邀请码，管理员批量生成）/ **封闭**（仅管理员签发，私服）。人类 = 账户（用户名+密码）→ 绑定 `player` 实体；agent = 世界级注册开关 + 可选邀请码 → 创建 `agent` 实体并发放凭据。凭据可吊销（管理员吊销 agent，玩家自助改密/注销）；`read` 档围观可公开免注册（世界开放时） |

### C 组（文档补充）

| # | 决策点 | 结论 |
|---|---|---|
| C1 | 装备/格子 | 文档补示例：装备栏/背包格子由玩法包用 `attrs` 自管（如 `equipped: [...]`），内核只保证持有关系正确 |
| C2 | 玩法包重载 | 玩法包**跟随内核**（AstrBot 插件）整体重载，不做单包热重载 |
| C3 | 动作可见性 | 实体可用动作 = **kind 声明列表 ∪ 全局注册表**；handler 可返回空菜单隐藏动作（如战斗只对敌对实体出现） |

## 架构

```
                    玩法包(worlditor_play_*)
                            │ 注册表 API（唯一入口，锁内执行）
                            ▼
  Agent(任意MCP客户端) ──┐                        ┌── 玩家 WebUI
  （连接身份验证）      ├─ MCP 唯一动作通道 ──────┤ （WebUI 本身也是 MCP 客户端）
   token → 实体          │  工具 = 引擎原语薄封装    │  同一套工具/认证/返回协议
  （kind=agent/player）  └───────────┬─────────────┘
                                    ▼
                          WorldEngine（实例锁内变更）
                                    ▼
                WorldStore（SQLite WAL，启动全量载入内存）

── REST（非动作，仅 UI 渲染基础设施）：只读状态快照（地图/场景/背包）· SSE 事件流 ·
   web 静态资源（玩法包组件）· admin 管理端点 ──
```

| 新增/变更 | 说明 |
|---|---|
| `world/v4model.py` | ItemDef / Entity（统一模型）/ UiBlock / Effect / 交互协议（v3 模型保留扩展） |
| `world/v4engine.py` | v4 引擎：物品 / 实体 / 交互 / 广播动作原语；身份化实体持久化；effects 结算；事件总线与 on_tick 调度；**地图编辑原语**（地块/连接/地图/实体字段，admin 用）；**事件流订阅**（subscribe/unsubscribe，SSE 出口）（v3 的 engine.py 保留过渡） |
| `world/v4store.py` | v4 存储：新增表 entities / items / inventories / play_data / world_log / **accounts / tokens / invite_codes（v4.1 身份）**；**与 v3 表同库共存**（v3 表结构沿用、数据共享，无迁移） |
| `world/identity.py` | 身份服务（B13）：账户 / token 三档 / auth_mode 三模式 / 邀请码 / 改密 / 吊销（纯逻辑，不依赖 AstrBot） |
| `world/play/` | 玩法包发现加载器 + `WorlditorPlayAPI` 实现 + 异常隔离 |
| `world/mcp/` | 进程内 MCP server（v4.1 已落地）：`__init__.py` 7 个世界工具 + 连接身份读取；`http.py` 认证中间件（_meta 注入）+ 独立 uvicorn 服务；`stdio.py` 独立进程入口 |
| `api/` | **非动作端点**（v4.1 已落地，前缀 `/world/v4/` 与 v3 共存）：`auth_routes.py` 身份注册/登录/凭据；`snapshot.py` 只读快照（state/scene/bag）；`sse.py` 事件流；`admin.py` 管理端点（地图编辑含实体放置）；`static.py` 玩法包 web 资源；`v4common.py` token 提取与鉴权 helper。**无动作端点**（动作统一走 MCP，B10） |
| `main.py` | 装配 v3 + v4（同库共存）+ 身份服务 + MCP server（HTTP 按配置启动） |
| `demo_play/` | 内置参考玩法包（随内核分发，充当 SDK 模板，可删除）；v4.1 新增出生礼包演示（响应 place_entity 编辑事件） |
| `webui/` | 独立 Vue3 + Vite 前端（移动端优先），v4.1（待落地） |

## 数据模型 v4

### 实体（世界的唯一居民概念，B12）

**世界只有两个概念：地块与实体。** 玩家、AI agent、NPC 都是实体——只是 kind 不同：

- `kind="player"`（内置）：**人类玩家**，经 WebUI 控制；可被认证绑定、有背包、位置持久化。
- `kind="agent"`（内置）：**AI agent**，经 MCP 控制；同上。
- 其他 kind 由玩法包注册（`merchant` / `vending_machine` / `door` / `sign` / `workshop`...）：**布景与内容实体**，由地图编辑放置、行为由玩法包扩展。"饮料商人"与"自动售卖机"在功能/表现上无本质区别——差异仅是 kind、文本描述与玩法包为 kind 注册的行为。

```python
@dataclass
class Entity:
    id: str                    # uuid4 hex（B5）
    map_id: str
    row: int
    col: int
    kind: str                  # 注册表键：player / agent（内置）/ 玩法包扩展
    name: str                  # 放置时设定（kind 提供默认），UI 显示主元素（B1）
    desc: str
    attrs: dict                # 玩法数据（hp/exp/gold/level/equipped...），内核不解释
    state: dict                # 门开/关、库存、血量...（玩法包自管）
    # 身份化字段（仅 kind=player/agent 有效）：
    user_id: str | None = None         # 账户/实例标识（联邦预留）
    last_active_ts: float = 0.0        # 在线状态
```

- **身份化实体**（kind=player/agent）：可被认证绑定（WebUI token / MCP 凭据 → 实体）、有背包（inventories 按 entity_id）、位置持久化、`user_id` 标识账户。
- **布景实体**：地图编辑放置（admin），玩法包注册 kind 与行为（交互动作 / tick 状态机：巡逻、开合、复活均以状态机驱动已放置实体，不涉及生成/移除）。
- 一个地块可挂多个实体；实体列表保持简单（名称 + 标签），点击进入交互弹窗。
- 实体放置 / 移除 / 属性编辑 = 地图编辑 API（admin 权限），WebUI 编辑视图在地块详情中管理（v4.1）。

### 物品（定义与持有分离）

```python
@dataclass
class ItemDef:
    id: str
    name: str
    desc: str
    icon: str = ""             # 可选，后议（B1：UI 以名称+标签展示）
    stackable: bool
    use_action: str | None     # 玩法包注册的 use 交互动作（如 "eat"/"craft"/"equip"）
    attrs: dict                # 玩法数据（价格/属性/配方钩子）
```

持有：`inventories(entity_id, item_id, count, attrs_json)`——`attrs_json` 承载个体差异（强化等级、耐久、附魔）。

**装备/背包格子（C1）**：内核不提供槽位/格子概念。装备栏、背包格由玩法包用 `attrs` 自管（示例：`equipped: ["sword_01"]`、`bag_size: 20`），内核只保证持有关系正确（`give/take/count`）。

### 表结构

```sql
-- v3 表删除重建：maps / locations / templates / world_meta（结构沿用 v3，无数据迁移）
-- 新增：
CREATE TABLE entities (              -- 世界唯一居民概念（B12）：玩家/agent/布景实体统一
    id TEXT PRIMARY KEY,             -- uuid4 hex
    map_id TEXT NOT NULL,
    row INTEGER NOT NULL,
    col INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- player / agent（内置）或玩法包注册的 kind
    name TEXT NOT NULL,
    desc TEXT NOT NULL DEFAULT '',
    user_id TEXT,                    -- 身份化实体：账户/实例标识（联邦预留）
    attrs_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    last_active_ts REAL NOT NULL DEFAULT 0
);
CREATE INDEX idx_entities_pos ON entities(map_id, row, col);
CREATE TABLE items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    desc TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '',
    stackable INTEGER NOT NULL DEFAULT 1,
    use_action TEXT,
    attrs_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE inventories (
    entity_id TEXT NOT NULL,         -- 身份化实体（kind=player/agent）的背包
    item_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    attrs_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (entity_id, item_id)
);
CREATE TABLE play_data (              -- 玩法包通用 KV（namespace 隔离）
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE TABLE world_log (              -- 事件日志：围观 / 回放 / 排行数据源
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    entity_id TEXT,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE INDEX idx_world_log_entity ON world_log(entity_id);
-- world_log 容量：上限 5000 条，写入时触发清理（B3）
```

## 引擎动作原语（v4 新增，协议无关）

```python
# 物品（按实体）
give_item(entity_id, item_id, count=1, attrs=None) -> int       # 返回持有数
take_item(entity_id, item_id, count=1) -> bool
count_item(entity_id, item_id) -> int
list_inventory(entity_id) -> list[dict]                         # {item_id, def, count, attrs}

# 实体（地图内容：放置/移除 = 编辑操作（admin）；身份化实体移动走 move；布景实体移动走 move_entity）
place_entity(kind, map_id, row, col, name=None, desc=None, attrs=None, state=None) -> Entity
remove_entity(entity_id) -> None
move(entity_id, direction, path=None) -> SceneView              # 身份化实体按路径移动（v3 语义，加权抽目标；目标地块有 block_move 实体则拒绝）
move_entity(entity_id, map_id, row, col) -> None                # 直接坐标（玩法包行为驱动；传送语义，不做阻挡检查）
list_entities(map_id=None, row=None, col=None) -> list[Entity]
set_attrs(entity_id, patch: dict) -> None                       # 合并写 attrs
get_attrs(entity_id) -> dict
set_state(entity_id, patch: dict) -> None                       # 合并写 state（门开/关…玩法包自管）
get_state(entity_id) -> dict
list_actions(target_id) -> list[MenuButton]                     # 可用动作（C3：kind 声明 ∪ 全局注册表）

# 广播（B2：scope=world 消耗广播道具 + 冷却）
say(entity_id, text, *, scope="cell") -> None
#   cell 级：不限制；world 级：消耗 1 个内置广播道具（喇叭）+ 每人 30s 冷却（管理员豁免）
#   scope=world 时道具不足 / 冷却中 → 抛 WorldError（消息可直接展示）

# 交互（WebUI / MCP 的公共入口；发起者与目标都是实体）
interact(entity_id, target_id, action, args=None, *, item_id=None) -> InteractionResult
#   item_id：物品 use 交互（如吃苹果 use_action="eat"），结算后触发 on_item_used
```

## 玩法包规范

### 目录与元数据

```
data/
├── plugins/
│   └── astrbot_plugin_worlditor/        # 内核（本插件；内部 demo_play/ 属插件自身文件，安全）
└── plugin_data/
    └── astrbot_plugin_worlditor/        # worlditor 数据目录（StarTools.get_data_dir）
        ├── world.db
        └── plays/                       # 玩法包根目录（与框架插件目录隔离，B6）
            └── worlditor_play_dungeon/  # 社区玩法包：worlditor_play_* 前缀，启动时自动发现
                ├── play.yaml                    # 元数据
                ├── main.py                      # 入口：def setup(api, context) -> None
                ├── data/                        # 物品/对话树/配方/货单（json/yaml）
                ├── web/                         # 前端资源：自定义界面组件（B9，v4.1），静态托管
                └── assets/                      # 静态资源（后议）
```

`play.yaml`：

```yaml
name: worlditor_play_dungeon
display_name: 地下城
version: 0.1.0
author: ...
desc: ...
requires:
  worlditor: ">=4.0.0"
  plays: []              # 依赖其他玩法包（版本可写 ">=x.y.z"；v4.0 仅声明，解析 v4.2）
icon: assets/icon.png
```

### 发现加载流程（worlditor `initialize()` 时）

1. 扫描 `demo_play/`（内核自带）与 `<数据目录>/plays/` 下 `worlditor_play_*` 目录。
2. 校验 `play.yaml` 与 `requires`（v4.0 只校验 worlditor 版本；`plays` 依赖声明保留，解析与加载顺序 v4.2 实现）——不兼容记日志跳过，不阻断内核。
3. `importlib` 加载 `main.py`，调用 `setup(api, context)`（`context` 为 AstrBot `Context`，玩法包可选使用 LLM / 发消息等 AstrBot 能力）。
4. 每个玩法包一个独立 `WorlditorPlayAPI` 实例：**namespace 隔离**（kv / world_log 带 play id）、**异常隔离**（玩法包注册的 handler 统一包 try/except + 日志，不拖垮内核）。
5. 重载（C2）：玩法包**跟随内核**（AstrBot 插件）整体重载；`terminate()` 时若有 `teardown(api)` 则调用（玩法包可选）。

### WorlditorPlayAPI（玩法包唯一入口，`astrbot_plugin_worlditor.api`）

```python
class WorlditorPlayAPI:
    # 注册
    def register_item_def(self, item: ItemDef) -> None
    def register_entity_kind(self, kind: str, *, block_move=False,
                             interactions=(), tick=False, label=None) -> None
                                 # label: kind 标签文案（B1，如 "NPC"/"物品"）
    def register_interaction(self, action: str, handler: InteractionHandler,
                             *, label: str | None = None) -> None
                                 # label 缺省取 action；同步 handler 亦兼容（内核自动 await）
    def register_world_event(self, event: str, handler: WorldEventHandler,
                             *, interval: float = 0.0) -> None
                                 # interval 仅 on_tick 有效（各自间隔，A3）
    def register_ui_component(self, name: str, web_entry: str) -> None      # B9
    def register_ui_hook(self, block_kind: str, position: str,
                         provider: UiHookProvider) -> None                  # B9（before/after/replace）

    # 只读
    def get_entity(self, eid) -> Entity | None
    def list_entities(self, map_id=None, row=None, col=None) -> list[Entity]
    def get_location(self, map_id, row, col) -> Location | None
    def get_map(self, map_id) -> WorldMap | None
    def list_actions(self, target_id) -> list[MenuButton]                   # C3

    # 玩法数据（play_data 表，namespace 自动 = 玩法包 id）
    def kv_get(self, key, default=None)
    async def kv_set(self, key, value) -> None

    # 引擎动作（走原语，锁内执行；均按 entity_id）
    def give_item / take_item / count_item / list_inventory
    def move_entity                                     # 直接坐标（行为驱动）；实体放置/移除是地图编辑（admin）
    def set_attrs / get_attrs / set_state / get_state / say / interact
```

玩法包拿不到引擎内部对象，只能通过 API 原语操作；API 版本随内核版本绑定。

## 交互协议（玩法与 UI 之间的契约）

```python
@dataclass
class InteractionRequest:
    entity_id: str               # 发起者（身份化实体）
    target: Entity | None        # 目标实体（含玩家/agent 实体，如查看角色卡）
    item_id: str | None          # 物品交互（use）
    action: str
    args: dict

@dataclass
class MenuButton:
    label: str
    action: str                # 下一个交互动作
    args: dict = {}

@dataclass
class UiBlock:                 # 内核按 schema 渲染，玩法包不画界面
    kind: str                  # "text" | "menu" | "form" | "list" | "confirm" | "character" | "custom"
    title: str = ""
    text: str = ""
    fields: list[dict] = ()    # form: {name, label, type, required}
    items: list[dict] = ()     # list: {label, value, action?, args?}
    actions: list[MenuButton] = ()
    # character（B1，角色卡）：{avatar?, attrs: [{label, value}]} —— 人物实体的弹窗形态
    # custom（B9，自定义界面）：{component: "namespace.name", props: {...},
    #   fallback_text: "..."} —— 引用玩法包注册的组件；fallback_text 供 MCP 侧降级
    blocks: list["UiBlock"] = ()   # 子块（B9 界面钩子注入点；内核渲染时按序展开）
```

### 界面扩展机制（B9）

| API | 作用 |
|---|---|
| `register_ui_component(name, web_entry)` | 注册自定义界面组件（Web Component 封装，入口为玩法包 `web/` 下的静态资源），供 `custom` 块引用 |
| `register_ui_hook(block_kind, position, provider)` | 向已有界面块注入子块：`position ∈ {"before", "after", "replace"}`；before/after 追加内容，**replace 整体替换目标块渲染**（provider 返回 `list[UiBlock]`，通常为单个 `custom` 块） |

- **组件协议**：玩法包前端组件为 Web Component（框架无关），props 经 attribute（JSON）传入；组件通过身份化 bridge 调用世界 API（与当前操作者同权限，play 档动作），可与玩法包后端交互（经 `api` 上下文）。
- **降级规则**：`custom` 块必须带 `fallback_text`——MCP 侧（agent）与 WebUI 组件加载失败时显示文本；钩子注入的子块在 MCP 侧按各自 kind 序列化为文本。
- **信任与安全**：玩法包前端组件与玩法包后端同信任级别（玩法包本可执行任意代码）；bridge 仅暴露身份化动作原语，不含管理操作（admin 动作仍走管理端）。

@dataclass
class Effect:                  # 世界变更原语（内核结算，不信任玩法包直接改）
    op: str                    # give_item/take_item/move/move_entity/set_attrs/say
                               # （= 引擎原语子集；传送 = move_entity 特例，无独立 teleport）
    args: dict

@dataclass
class InteractionResult:
    text: str = ""
    ui: UiBlock | None = None
    effects: list[Effect] = ()
```

- handler 签名：`async def handler(api, req: InteractionRequest) -> InteractionResult`（同步 handler 亦兼容，内核自动 await；handler 异常被隔离并转为可展示的 WorldError）
- 交互流：WebUI / MCP → `engine.interact(entity, target, action, args)` → 内核查实体可用动作（C3：kind 声明列表 ∪ 全局注册表；handler 可返回空菜单隐藏动作）→ 玩法包 handler → **内核结算 effects**（按 op 执行原语，校验权限与合法性）→ 广播 `on_interact` → 返回 result。
- **返回协议（B10）**：`InteractionResult` 序列化为结构化 JSON（`text` + `ui`）经 MCP 工具返回——WebUI 渲染 `ui`（含 custom 组件/钩子注入），agent 消费 `text`；`custom` 块取 `fallback_text` 兜底。
- 弹窗形态（B1）：简单实体 → text/form/menu 弹窗；人物实体 → `character` 角色卡（头像 + 属性）；玩法包可注入子块 / 引用自定义组件 / 以 replace 重写渲染（B9）。
- 聊天侧不渲染（A2：人类玩家走 WebUI；MCP 返回 result 文本给 agent 消费，`custom` 块取 `fallback_text`）。

## 事件总线（单一事件源）

**内核只有一个事件总线**：玩法包订阅它（响应逻辑），SSE 是它的一个序列化出口（推送 WebUI）。事件表统一如下，不存在两套事件系统：

```python
EVENTS = {
    "on_tick": (interval_seconds, handler(api, dt)),       # 世界心跳（A3：单循环+各自间隔+异常隔离）
    "on_entity_move": handler(api, entity, from_pos, to_pos),
    "on_entity_enter": handler(api, entity, map_id, row, col),
    "on_say": handler(api, entity, text, scope),
    "on_interact": handler(api, request, result),
    "on_item_used": handler(api, entity, item_id, count, args, result),
    "on_entity_removed": handler(api, entity),
    "on_entity_changed": handler(api, entity, changed),    # 实体状态/属性变化
    "on_world_edited": handler(api, what),                 # 地图被编辑（管理员操作）
}
```

- 事件/tick 的 handler 内**直接调用 API 原语（命令式，A1）**——无展示需求，副作用即时生效。
- 事件带 `cause`（谁触发的）；`origin`（实例标识）为联邦（v5）预留。
- on_tick 调度（A3）：内核单循环按最小注册间隔跑；各 handler 记录上次执行时间，未到期跳过；串行执行 + 异常隔离，世界动作不排队等玩法。
- **SSE = 事件总线的序列化出口**：WebUI 订阅其中的公共事件（on_say / on_entity_move / on_entity_enter / on_interact / on_entity_changed / on_world_edited），事件体序列化为 JSON 推送；事件同时写入 world_log（历史）。事件流一个源，玩法包与 WebUI 看到的是一致的。

## MCP 服务与统一身份（A5 / B7）

- **形态**：worlditor 进程内启动 MCP server（FastMCP），**v4.1 后端已落地**。工具 = 引擎动作原语的薄封装（协议无关层零改动）。
- **传输**：
  - **streamable HTTP**（公网可达，供远程 MCP 客户端接入）：FastMCP `streamable_http_app()` + 认证中间件，由**独立 uvicorn 服务**承载（配置 `enable_world_api` / `world_api_host` / `world_api_port`，默认 6288；HTTPS 反向代理指向它，可配置限流 / IP 白名单）。
  - **stdio**（本地）：独立进程入口 `python -m astrbot_plugin_worlditor.world.mcp.stdio --db <world.db> --token <凭据>`（或环境变量 `WORLDITOR_DB` / `WORLDITOR_TOKEN`），一个连接绑定一个实体。
- **连接即身份验证**：
  - HTTP：认证中间件校验 `Authorization: Bearer <token>`（或 `?token=`），把 `{entity_id, tier}` 注入每个 JSON-RPC 请求的 `params._meta`；工具经 `ctx.request_context.meta` 读取（read 档无实体 → 工具不可用）。
  - stdio：启动凭据解析为固定实体（TokenInfo 绑定）。
  - 服务端验证后映射到 `kind="agent"` / `kind="player"` 的实体（身份化实体）；后续工具调用默认以该实体身份执行，无需每调用传 entity_id。agent 凭据经**自助注册**获得（B13），管理员可吊销。

### 身份注册与凭据（B13）

统一注册机制服务两类接入者，世界级配置三种模式（插件配置 `_conf_schema.json`）：

| 模式 | 配置 | 人类（WebUI） | agent（MCP/远程） | 适用 |
|---|---|---|---|---|
| 开放 | `auth_mode=open` | 用户名+密码自助注册 → 登录 token → 绑定/创建 `player` 实体 | 调用注册端点（agent 名 + 可选世界级 agent 开关）→ 创建 `agent` 实体 + 凭据 | 公共服 / 测试服 / 联邦开放世界 |
| 邀请码 | `auth_mode=invite` | 注册需邀请码（管理员批量生成） | 同上，需邀请码 | 半开放社区服 |
| 封闭 | `auth_mode=closed` | 仅管理员建号 | 仅管理员签发凭据 | 私服 |

- **端点（REST，`/world/v4/` 前缀，v4.1 已落地）**：`register`（人类，可带 `admin_key`）/ `register-agent` / `login` / `logout` / `change-password` / `revoke`（管理员吊销）/ `read-token`（开放模式围观）/ `invite-codes`（管理员生成/查看）。
- **管理员**：配置 `admin_key`——注册时提供它的账户自动成为 admin（admin 档 token）；admin 注册通道豁免模式限制（否则 invite 模式下第一个管理员无法注册）。
- **凭据**：token 绑定实体（`player`/`agent`），分档（B4：`read`/`play`/`admin`）；`read` 档围观在开放模式下可公开获取（免注册），`play` 档必须注册。
- **防滥用**：注册限流（IP/时间窗，部署层）、agent 注册需世界级开关（`allow_agent_register`）、邀请码批量生成与吊销、玩家自助改密/注销。
- **联邦场景**：远程 AstrBot operator 按世界模式自助注册 agent 凭据 → 填入 MCP client 配置，全程无需管理员介入；管理员只负责模式配置、邀请码与吊销。
- **agent 与玩家没有本质区别**（A5 / B10 / B12）：两者都是实体（kind=agent / kind=player），玩家 = WebUI 点击操作自己的实体；agent = MCP 调用工具行动。**两者走同一条 MCP 动作通道**——同一套工具、同一套身份认证（token → 实体）、同一份返回协议；WebUI 本身就是一个 MCP 客户端（人类凭据连接）。行为在协议层不可能分叉。
- **返回协议（B10）**：世界工具返回**结构化 JSON**：`{text: 文本（LLM/兜底消费）, ui: 结构化界面（UiBlock，UI 渲染消费）, effects: 已结算变更}`——agent 读 `text`，WebUI 渲染 `ui`，一次实现两端复用。场景类工具（`world_look`）返回场景结构（位置/描述/路径列表），UI 据此渲染方向按钮，LLM 据此决策移动。
- 工具集（v4.1 初版，**已落地**）：`world_look` / `world_move` / `world_say` / `world_bag` / `world_use` / `world_interact` / `world_who`（同地块实体）。
- **无插件加入（联邦基础）**：远程 AstrBot **不需要安装 worlditor**——只需在其 MCP client 配置中注册本世界的 MCP server（地址 + agent 凭据），其治理的 agent 即获得世界工具、以独立实体身份加入世界；任意标准 MCP 客户端（非 AstrBot 生态）同样适用。
- AstrBot 自身 agent 接入（本地）：既可通过内置 MCP client 挂 worlditor 的 MCP server（stdio 配置），也可直接注册 LLM 工具（`register_agent` 按 agent 绑定，闭包捕获身份）；全局工具路径从事件会话 persona 解析身份（`resolve_event_conversation_persona_id`）。两条路径最终都落到 entities 表同一身份。

### REST 非动作端点（B10，v4.1 后端已落地）

前缀 `/world/v4/`（与 v3 端点共存过渡，v4.1 完成 WebUI 后 v3 移除）：

| 端点 | 鉴权 | 说明 |
|---|---|---|
| `GET /world/v4/state` | read+ | 全量快照：maps / locations / entities |
| `GET /world/v4/scene?entity_id=` | read+ | 实体场景（围观任意；play 档缺省自己） |
| `GET /world/v4/bag?entity_id=` | play+ | 背包（play 档自己；admin 任意） |
| `GET /world/v4/events?token=` | play+ | SSE 事件流（事件总线 subscribe 出口，B11） |
| `GET /world/v4/plays/<play_id>/web/<path>` | read+ | 玩法包 web/ 静态资源（B9，路径穿越防护） |
| `POST /world/v4/admin/location/*` 等 | admin | 地块 CRUD / 连接 / 地图 / 实体放置与编辑（B8） |

## WebUI v1（Vue 3 + Vite，移动端优先）✅ 已落地

### 部署与鉴权

- 仓库子目录 `webui/`；**插件内置托管（默认）**：`webui/dist/` 构建产物随插件发行，6288 世界服务挂载为根路径静态资源（免认证加载登录页，API 路由优先）——开启 `enable_world_api` 后访问 `http://<主机>:6288/` 即完整 WebUI；也可独立部署（`VITE_WORLD_API` 指向世界服务 + 后端 `allowed_origins`）。
- 配置：`enable_world_api` / `auth_mode`（open/invite/closed，B13）/ token 三档（B4：`read` / `play` / `admin`）/ `allowed_origins`。
- 客户端 token 存 localStorage（与 dashboard 会话隔离）；**注册/登录**（用户名+密码，B13）后获得 play 档凭据并绑定 `player` 实体；开放模式下 `read` 档可公开围观。
- 人类玩家的正式入口（A2）：IM 侧不做游玩命令。

### 页面结构（移动端优先，桌面同套代码）

| 路由 | 页面 | 要点 |
|---|---|---|
| `/` | 世界 | 触屏网格地图（SVG/Canvas）：单指拖动、双指缩放、点击地块/实体；**实体显示 = 名称 + kind 标签**（B1），玩家位置 + 同地块角色条；顶栏当前地块名/描述 |
| `/me` | 角色 | 面板：自己的实体（kind=player）属性，attrs 按玩法包声明展示（v4.1 先通用键值列表），头像（emoji/后议） |
| `/bag` | 背包 | 物品网格 + 点击弹物品菜单（use/详情，按钮来自注册表） |
| `/log` | 日志 | SSE 事件流（say/进入/交互），切换"当前地块/全图" |

**交互弹窗**（核心组件，B1）：点击实体 → 底部抽屉（实体名 + kind 标签 + desc → 动作按钮列表 → 结果页）。结果按 `UiBlock` schema 渲染：`text` / `menu` / `form` / `list` / `confirm` 通用渲染；**人物实体走 `character` 角色卡**（头像 + 属性列表）。全屏化适配小屏。弹窗形态按实体类型可扩展（本版本先覆盖：文本提示 + 按钮表单 + 角色卡）。

**玩法包界面扩展（B9）**：玩法包 `web/` 静态资源由内核 API 托管（`/world/play/<play_id>/web/*`，需 token）；WebUI 以 import map 注册组件入口并动态 import。`custom` 块 → 实例化玩法包组件（Web Component，props 经 attribute 传入）；`ui_hook` 注入的子块在渲染时展开进目标块的 `blocks`（before/after 追加，replace 整体替换）。组件经身份化 bridge 调世界 API（play 档权限）。

**SSE 实时（B11）**：`GET /world/events?token=...`（Server-Sent Events 流，浏览器 EventSource 原生消费）。**不轮询 MCP**——动作是请求-响应（点一下等结果），实时感知是推送。SSE 是**事件总线**（见「事件总线」节）的序列化出口：玩法包与 WebUI 看到的是同一份事件。

- 订阅范围：WebUI 订阅公共事件（on_say / on_entity_move / on_entity_enter / on_interact / on_entity_changed / on_world_edited），事件体带实体、位置、时间。
- **UI 更新策略**：事件驱动**增量更新**（本地缓存：角色位置、日志追加、说话气泡、实体状态）；无法增量表达的事件（如 `on_world_edited`）触发一次状态快照拉取（REST 只读端点兜底）。
- **断线重连**：EventSource 自动重连；重连后先拉一次状态快照补齐遗漏（事件驱动为主、快照兜底，不做高频轮询）。
- **节流**：同一角色连续移动合并为位置更新事件，避免高频刷屏；事件按订阅者过滤（当前地块 / 全图）。
- **在线状态**：`last_active_ts` 由动作与 SSE 连接活动维护，**不设独立心跳端点**（SSE 长连接本身就是在线信号）。

## 种子世界 v4

- 保留 v3 的 41 地块小镇（广场 · 步行街 · AstrBot大道 · 开源小区 · AstrBot大学 · 迷雾森林）。
- 新增演示实体（静态，A4；作为地图种子数据直接放置，B8）：广场「商贩·阿福」（kind=merchant，talk/trade，货单含苹果）、步行街「告示牌」（kind=sign，read）、迷雾森林入口「木门」（kind=door，open，block_move，演示状态变更）。
- 新增演示物品：苹果（stackable，use_action 由 demo 玩法包注册）、**喇叭**（内置广播道具，B2：say world 消耗）。
- `demo_play/`（B6）参考玩法包：演示 item / entity_kind / interaction / event 完整链路，充当 SDK 模板，用户可删。

## 联邦（v5，远景；本期只保证预留）

**主体形态 = MCP 通道联邦**：公网部署的 worlditor 世界即服务器；远程 AstrBot 实例（**无需安装 worlditor**）与任意标准 MCP 客户端，经 MCP 连接认证后，其治理的 agent 以独立角色身份加入同一世界——"一个公网世界，多实例 agent 同场"。

- **一致性**：世界权威始终在服务器端引擎（单实例锁不变）；远程客户端只发动作，无共享内存——v4 的同步模型假设不破。
- **身份**：每 agent 独立凭据 → 独立实体（kind=agent，uuid4）；`user_id` 承载实例标识；凭据经自助注册获得（B13），管理员可吊销。
- **安全前提**（用户明确：只考虑公网服务器部署）：HTTPS + 认证 + 限流 / IP 白名单；凭据经自助注册机制（B13）发放，管理员只做模式配置、邀请码与吊销。
- **可选增强（后议，不做承诺）**：SSE 事件订阅对远程客户端开放（远程实时感知）；`RemoteEngine` 客户端模式（全功能远程：地图编辑/管理，要求双方都装 worlditor）；跨实例事件广播。
- **v4 已落实的预留**：id 全局唯一、事件带 `cause`/`origin`、引擎动作原语即远程契约（协议无关层零改动）、MCP 身份验证机制即跨实例接入通道。

## 路线图

- **v4.0 底子内核**（✅ 完成）：v4model / v4store（新表，与 v3 同库共存）/ v4engine（原语 + 事件总线）/ 交互原语与 effects 结算 / 玩法包加载器 + `WorlditorPlayAPI` / 广播道具与冷却 / 身份化实体持久化 / 版本统一 / 种子世界 v4（含放置实体）+ `demo_play/` / 全套单测。**无 HTTP 动作端点**（调试页走进程内 Python 动作，B10）。
- **v4.1 独立 WebUI + MCP（唯一动作通道）**（✅ 完成）：进程内 MCP server（streamable HTTP 独立服务 + stdio 入口，连接身份验证，7 工具结构化返回）、**身份注册**（auth_mode 三模式 + admin_key + token 三档 + 邀请码 + 改密/吊销）、REST 非动作端点（只读快照 / SSE 事件流 / admin 地图编辑含实体放置 / 玩法包 web 资源）、v4 引擎地图编辑原语与事件流订阅、**界面扩展 apply_ui_hooks（B9 before/after/replace，MCP 返回前应用）**、**WebUI（Vue3 + Vite 移动端优先四页 + 登录注册 + 交互弹窗 UiBlock 渲染 + SSE 增量更新 + 轻量 MCP client）**、版本统一 v4.1.0。custom 组件动态加载留 v4.2。
- **v4.2 玩法 SDK 定型**：开发者文档（docs/PLAY_DEV.md）+ 玩法包依赖解析打磨 + 社区参考玩法。
- **v5 联邦**：MCP 公网通道（streamable HTTP）+ **agent 自助注册与凭据管理（B13）** + 限流；可选：SSE 对远程开放、`RemoteEngine` 客户端模式。
