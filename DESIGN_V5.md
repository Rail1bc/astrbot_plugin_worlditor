# worlditor 重构设计 v5（定稿）——项目重开：内核纯数据，玩法包承载一切

> 状态：**设计定稿**（2026-08，D1–D13 已确认）。基于 v0.3.0 实际运行经验与多轮
> 讨论重开设计。配套：DESIGN_V4.md（现行架构，v0.3.0 已实现）。v5 为**开发阶段
> 完全重构**：保留有价值的代码（复制复用），行为层整体以"玩法包"身份重写；
> 旧数据不保留（D13），零历史债务。本文件是唯一权威设计文档。

## 0. 一句话定位

**插件 = 世界的"数据 + 编辑 + 身份 + 传输 + 玩法包管理"平台；一切行为、规则、
工具与界面由玩法包提供。** 插件内置多个领域玩法包（可停用/替换/删除）确保开箱
可玩，兼作 SDK 模板。

- 内核不做任何玩法判断：不知道"玩家怎么移动、视野多大、怎么说话、页面长什么样"
- 内核保证：数据永远正确、身份永远可验、扩展永远有入口、管理永远在手

## 1. 架构总览

```
玩法包 worlditor_play_*（内置领域包 ×5 + 社区包）
  ├─ 行为：on_tick 状态机 / 事件订阅 / 交互 handler / 自定义事件
  ├─ 规则：视野 / 广播 / 行为编排 —— 读数据 → 调内核原语
  │        （移动默认由内核提供，玩法包可 override/disable，D11）
  ├─ 工具：register_tool 注册 MCP 工具（身份经 api.caller() 获取，内核裁定权限）
  └─ 视图：register_view 注册页面（UiBlock 协议，WebUI 渲染）
        │  注册表 API（唯一入口，锁内执行，异常/namespace 隔离）
        ▼
内核 worlditor（v5）
  ├─ 事实层：地块/连接/模板 + 实体（字段化）+ 物品定义（字段化）
  │          + 玩法数据 KV + 日志（SQLite WAL，全量内存快照；无背包持有表）
  ├─ 原语（默认实现，全部可被玩法包覆盖/禁用，D11）：
  │    place_entity / remove_entity / move_entity /
  │    move（路径移动：读 connections → 抽目标）/
  │    set_data / get_data / interact（handler 命令式调原语，无 effects，D12）
  │    （无背包原语——D8 持有下沉；无 say——D1）
  ├─ 身份：账户 / token 三档 / auth_mode / 邀请码 / 吊销（不可下沉）
  ├─ 编辑：地块/连接/地图/实体与物品定义编辑（admin 权限，含多地图）
  ├─ 管理：玩法包 list / enable / disable / uninstall + 状态持久化（内置能力）
  ├─ 传输：MCP（streamable HTTP + stdio，连接即身份验证）+
  │        REST 非动作端点 + SSE + 内置 WebUI（玩法包视图宿主）
  └─ 协议：UiBlock / InteractionResult（text + ui，无 effects，D12）/ 事件总线
            （基础设施：订阅/分发/日志/SSE 序列化支持任意事件名；事件语义由玩法包定义）
```

## 2. 内核：能力与红线

### 2.1 事实层（数据，无行为语义）

| 数据 | 说明 |
|---|---|
| 地块 / 连接 / 模板 | 多地图；4 方向槽位 + 平行路径 + 加权目标 + 分时段文本（v3 模型原样保留） |
| 实体 | 字段化统一模型（见 §3.1），位置持久化 |
| 物品定义 | 字段化 ItemDef（见 §3.2），**仅定义不持有** |
| 玩法数据 KV | play_data（namespace 隔离） |
| 世界日志 | world_log（5000 上限） |
| 身份 | accounts / tokens / invite_codes |

持久化：SQLite WAL + 启动全量内存快照 + 实例锁 + 级联清理。

### 2.2 基础原语（行为地基）

| 原语 | 语义 | 默认实现 |
|---|---|---|
| `place_entity` / `remove_entity` | 实体存在性（**admin 编辑**，无 spawn/despawn） | 内核 |
| `move` | 路径移动（身份化实体） | 内核：读 connections → 按权重抽目标（死引用剔除） |
| `move_entity(map,row,col)` | 直接位移（行为驱动，传送语义） | 内核 |
| `set_data` / `get_data` | 字段读写（合并写 / 全量读） | 内核 |
| `interact` | 交互通道：handler 命令式调内核原语（无 effects 清单，D12）；结果 = text + ui | 内核 |

所有原语默认实现由内核提供，但**可被玩法包覆盖/禁用**（§2.4，D11）。

### 2.3 红线（不可下沉）

| 红线 | 理由 |
|---|---|
| 事实模型与持久化（并发锁 / 级联清理） | 数据正确性：世界结构不会因玩法包代码漂移而失控 |
| 身份与凭据（token→实体、auth_mode、三档权限、吊销） | 安全：工具由玩法包注册后，"调用者是谁"仍由内核裁定 |
| 基础原语（含覆盖/禁用分派） | 行为层地基与能力治理 |
| 事件总线基础设施（订阅/分发/异常隔离/日志） | 机制留内核，事件语义与自定义事件开放给玩法包 |
| MCP 传输层 + 连接认证 + 身份注入 | 通道留内核，工具内容玩法包注册 |
| 地图编辑权限与编辑原语 | 世界内容治理（admin） |
| UI 渲染协议（UiBlock schema） | 协议留内核，视图内容玩法包提供 |
| 玩法包管理（list/enable/disable/uninstall） | 管理"扩展机制"本身，天然属于内核 admin 域 |

> **已下沉（不在内核）**：背包持有（D8）、说话与广播（D1）、玩法数据语义
> （字段由玩法包声明/读写）。
> **内核提供但玩法包可覆盖/禁用**：全部原语（D11）。

### 2.4 原语覆盖机制（D11）

| API | 语义 |
|---|---|
| `override_primitive(name, handler)` | 替换默认实现（handler 签名与原语一致）；每原语同时至多一个覆盖者，第二个报错（同 D2） |
| `disable_primitive(name)` | 关闭该能力（调用抛"该能力已被禁用"） |

- 覆盖范围：全部原语（place/remove/move/move_entity/set_data/get_data/interact）
  ——如"前进/后退"玩法包重写 move（基于实体朝向字段）；身份、管理、编辑权限
  不可覆盖（红线）。
- 覆盖/禁用状态**管理页可见**（哪个包覆盖了什么、谁禁用了什么）。
- 事件/日志照常记录（被覆盖的调用走同一分派入口）。

## 3. 数据模型

### 3.1 实体（字段化 + 分类，D9 / D10）

实体只保留**最小身份与位置**，一切数据以**字段**承载；实体类型（kind）可挂
**分类标签**，供玩法包精准选取一组实体类型。

```python
@dataclass
class Entity:
    id: str          # uuid4 hex
    kind: str        # 种类（player/agent 内置或玩法包注册）
    map_id: str      # 位置
    row: int
    col: int
    name: str        # 显示主元素
    desc: str = ""
    data: dict = {}  # 字段：kind 声明 ∪ 分类声明 ∪ 实例自定义（内核不解释）
    user_id: str | None = None   # 身份化实体绑定
    last_active_ts: float = 0.0
```

**三层次字段**：

| 层次 | API | 用途 |
|---|---|---|
| kind 声明字段 | `register_entity_kind(kind, ..., fields=[{name,label,type,default?}])` | 类型级 schema，UI 通用渲染（角色卡/编辑表单）；类型 str/int/float/bool/json |
| 向已有 kind 追加字段 | `add_kind_fields(kind, fields)` | 玩法包 B 给其他包的 kind 加字段（如 monster 加 poison） |
| 实例任意字段 | `set_data(entity_id, name, value)` | buff 等临时效果，无需声明；未声明字段 UI 降级通用键值 |

**分类标签（D10）**：`register_entity_kind(..., categories=("生物",))`——kind 挂
标签（宽松，无需预注册）；**分类字段** `add_category_fields("生物", [hp])` 使该
分类全部 kind 获得字段（运行时合并：kind 有效字段 = kind 声明 ∪ 所属分类声明）；
`list_kinds(category=None)` 精准选取（"给所有生物加血量" / "战斗目标 = 同地块生物"）。

**其他规则**：阻挡判定 `data["block_move"]` 优先于 kind 声明（门开/关玩法包写
字段）；实体无内置背包字段（D8）。

### 3.2 物品定义（D8：定义回内核，持有下沉）

物品与实体同构：**内核只定义物品类型，字段可玩法包扩展；持有（背包）下沉**。

```python
@dataclass
class ItemDef:
    id: str          # 类型键（如 apple），物品定义即"类型"
    name: str
    desc: str = ""
    data: dict = {}  # 字段（同实体字段机制）
```

- `register_item_def(item, fields=[...])` 定义物品类型；`add_item_fields(item_id,
  fields)` 向已有物品类型追加字段（如给苹果加 price）。
- 物品字段与实体字段**共用同一套"数据字段"设施**（schema 声明 / 合并 / UI 通用渲染）。
- **持有/背包不在内核**：谁持有多少、有限格子、堆叠、整理，全由玩法包实现
  （可存实体实例字段、KV 或自定义结构）。

## 4. 玩法包体系

### 4.1 注册面（内核 API）

| API | 内容 |
|---|---|
| `register_item_def` / `add_item_fields` | 物品类型定义与字段追加 |
| `register_entity_kind` / `add_kind_fields` / `add_category_fields` | 实体类型、字段、分类 |
| `register_interaction` | 交互动作 handler |
| `register_world_event` | 任意事件名订阅（on_tick 带间隔） |
| `register_ui_component` / `register_ui_hook` | 自定义界面组件 / 界面注入（before/after/replace） |
| `register_tool` / `register_view` | MCP 工具 / WebUI 页面 |
| `override_primitive` / `disable_primitive` | 原语覆盖 / 禁用（D11） |

### 4.2 运行时（读写 + 身份）

- 只读：实体/场景/地图/动作列表/背包（玩法包自己的数据）/字段/KV/`list_kinds(category)`
- 写：`set_data` / `get_data` / `move_entity` / `interact` / `emit`（自定义事件）
- `caller()`：当前调用者身份（MCP 工具 handler 用，权限内核裁定）
- 自有资源：`data/`（数据文件）、`web/`（组件入口）、kv namespace（隔离）

### 4.3 玩法包管理（内核内置，admin）

| 能力 | 说明 |
|---|---|
| list | 名称/版本/作者/desc/requires/**状态**（loaded/disabled/加载失败+错误详情） |
| enable / disable | 即时生效：复用 load_one / teardown / clear_play_registrations |
| uninstall | 删除社区包目录（内置包仅可停用） |
| 状态持久化 | enabled 标记落库，重启按标记加载（内置包默认启用，D5） |
| 整体重载 | 随内核（C2 保持：不做代码热重载） |

入口：admin REST 端点 + WebUI 管理视图（admin 档可见）+ 可选 MCP admin 工具。

### 4.4 玩法包基础设施（内核新增，前置条件）

| 能力 | 说明 |
|---|---|
| plays 依赖解析 | `requires.plays` 加载顺序保证（社区包可声明依赖领域包） |
| MCP 动态工具 | `register_tool`；同名工具冲突**报错拒绝**（D2） |
| 自定义事件 | `api.emit(event, **data)` + 任意事件名订阅；SSE/world_log 通用化——说话下沉的通道（D1） |
| 视图宿主 | `register_view(key, {title, icon, provider})`；WebUI 渲染玩法包视图（D7） |
| 数据字段设施 | 三层次字段 + 分类（§3.1） |

## 5. 行为归属（谁提供什么）

| 行为 | 提供者 |
|---|---|
| 路径移动（默认） | 内核 `move`（可被玩法包覆盖，D11） |
| 方向/朝向移动（前进/后退） | 玩法包 `override_primitive("move")` |
| 说话：cell 规则 / world 广播（喇叭+冷却） | social 包（D1：内核无 say；喇叭 = 内核物品定义 + 本包持有） |
| 背包模型 / 整理 / 物品 use 规则 | items 包（D8：持有全下沉） |
| 视野视图（3×3 或任意形态） | movement 包（register_view） |
| 玩家出生礼包 / 角色视图 | player 包 |
| 交互弹窗编排 / 动作菜单 | interaction 包 |
| 种子演示实体（商贩/告示牌/木门）的 kind 与交互 | interaction 包（实体本身由内核播种，D13） |
| 日志视图 | social 包 |
| 登录/注册/身份 | 内核 |
| 地图编辑 / 玩法包管理 UI | 内核（admin） |

## 6. 内置领域包（5 个，默认启用，D5）

| 玩法包 | 领域 | 贡献 |
|---|---|---|
| `worlditor_play_items` | 背包与物品使用（持有下沉，D8） | 背包模型自定（有限格子/单物品多格/堆叠/整理）、物品 use 规则、背包视图、world_bag/world_use 工具；注册基础物品定义（苹果等）并声明字段 |
| `worlditor_play_player` | 玩家 | 玩家实体行为、出生礼包、角色视图 |
| `worlditor_play_movement` | 移动与视野 | 默认移动 = 内核 move；视野视图（3×3）、world_look/world_move/world_who 工具；可按需 override move |
| `worlditor_play_interaction` | 交互 | 交互弹窗编排、动作菜单、world_interact 工具；注册种子演示实体的 kind 与交互（merchant/sign/door：talk/trade/read/open） |
| `worlditor_play_social` | 说话与广播 | cell 说话 / world 广播（喇叭物品自行定义 + 冷却自管）、world_say 工具、日志视图 |

**协作模型：事件驱动，零包间调用**——移动包更新位置 → 内核发 `on_entity_move`
→ 视野/日志包各自订阅刷新。包只依赖内核数据与事件，不依赖其他包存在与否；
删除任何包世界照常运行（只是少对应能力）。加载顺序只影响"能力何时可用"，
不影响正确性（事件在包加载后订阅，错过的事件由快照兜底）。

## 7. 项目重开形态与复用清单

### 7.1 重开形态（D4）

同仓库重开，版本继续 v0.4.0：git 历史保留（v0.3.0 随时可 checkout 复用），
代码结构按新架构重排，不新建仓库。

### 7.2 复用清单（从 v0.3.0 复制，已验证可用）

| 来源 | 去向 | 说明 |
|---|---|---|
| `world/v3model.py` | 原样 | 地块/连接/TextSchedule/模板——数据模型稳定 |
| `world/store.py` + `world/v4store.py` | 复制删减 | maps/locations/templates/world_meta + play_data/world_log + accounts/tokens/invite_codes 原样；entities 表字段化（data_json，删 attrs/state）；items 表仅定义（字段化，stackable/use_action 等玩法字段入 data）；**删除 inventories 表**（D8）；旧库数据不保留（D13） |
| `world/v4engine.py` 机制部分 | 复制删减 | move（保留为默认实现，走原语分派）/ move_entity / interact（handler 命令式，删除 effects 结算）/ 事件总线（开放任意事件名 + emit）/ 地图编辑原语 / 订阅；删除 say（D1）、give/take/count 与持有（D8）、7 工具注册；attrs/state → set_data/get_data 字段体系（D9）；新增原语分派（override/disable）与字段/分类注册表 |
| `world/identity.py` | 原样 | 身份红线 |
| `world/mcp/http.py` + `stdio.py` | 原样删减 | 传输/认证/世界服务端点（/auth /state /scene /events /admin）；删除内置 7 工具注册（改动态）、/bag（背包下沉）；/events 出口支持玩法包自定义事件通用 payload |
| `api/` | 复制 | admin 端点（地图编辑含实体放置）等非动作端点 |
| `pages/world/*`（v3 编辑页） | 原样 | 编辑能力 UI（补多地图支持 + 实体/物品字段编辑按 schema） |
| `webui/` 框架 | 复制改造 | App/路由/登录/token/MCP client/store/样式 → 视图宿主；页面内容改由玩法包视图提供 |
| `demo_play/` | 删除（D3） | 领域包即 SDK 模板 |
| 测试 | 迁移 | 数据层/身份/传输测试保留；物品/背包/移动/说话测试迁到各领域包 |

### 7.3 数据策略：空库重建，不保留（D13）

v5 是**开发阶段完全重构**：不兼容旧数据、不迁移、不备份、不写任何检测/兼容
分支——**零历史债务**。

- 启动即重建：旧库文件（`world.db` 及其 `-wal` / `-shm`）直接删除后建新库；
  旧账号 / 地图编辑成果 / 背包 / 日志全部不保留（`-wal`/`-shm` 必须同删，
  否则残留日志会被新库误当 WAL 应用，出现幽灵数据）。
- 无 `schema_version` 检测与迁移逻辑；旧代码仅存在于 git 历史（D4），主线
  不含任何 v4 兼容代码。
- 新库播种：41 地块 + 3 个种子演示实体（内核，实体 kind 与交互由 interaction
  包注册）；物品定义全部由玩法包注册（内核仅保留 D1 喇叭定义）。

## 8. 分阶段路线（每阶段可独立发布/验证）

```
M1 玩法包基础设施：管理（list/enable/disable/uninstall+持久化）、plays 依赖解析、
   MCP 动态工具、自定义事件、视图宿主、字段与分类设施、原语分派
   —— 内核能力"开放"，默认行为仍在内核（向后兼容）
M2 内核瘦身：删除 say / 7 个内置 MCP 工具 / 默认页面内容；WebUI 转视图宿主；
   move 保留为可覆盖的默认实现（D11）
M3 领域包逐个落地：items → player → movement → interaction → social（每包独立可测）
M4 验证与收尾：一个"替代玩法包"（如同方向延伸视野 / 朝向移动 override move）
   证明可替换；删除全部内置包的世界仍可编辑/浏览（管理页可见空态）；
   测试迁移完成；docs/PLAY_DEV.md；版本发布 v0.4.0
```

## 9. 决策记录（D1–D13，已全部确认）

| # | 决策点 | 结论 |
|---|---|---|
| D1 | 说话能力归属 | **内核无 say**：单地块说话与全图喇叭广播全部下沉玩法包（social 包：cell 说话、world 广播消耗喇叭+冷却；通道 = 自定义事件 emit；喇叭 = 内核物品定义 + 本包持有） |
| D2 | 同名工具冲突 | **报错并拒绝注册**（管理页可见错误，避免静默替换） |
| D3 | demo_play | **删除**（领域包兼作 SDK 模板，避免双份维护） |
| D4 | 重开形态 | **同仓库重开，版本继续 v0.4.0**（git 历史保留，旧代码可 checkout 复用） |
| D5 | 内置包默认状态 | **默认全部启用**，管理页可停用（停用有"将失去对应能力"提示） |
| D6 | ~~移动规则归属~~ | **已被 D11 取代**（移动回归内核默认实现，玩法包可覆盖） |
| D7 | 视图宿主形态 | WebUI 仅渲染玩法包视图；内核保留登录、token、数据通道与兜底"无视图"提示 |
| D8 | 物品与背包归属 | **定义回内核，持有下沉**：物品 = 内核 ItemDef（类型，字段化，玩法包可追加字段）；背包（有限格子/单物品多格/堆叠/整理）与持有关系全由玩法包自定；无 inventories 表 |
| D9 | 实体数据形态 | **字段化**：实体 = id/kind/位置/name/desc + data 字段（内核不解释）；kind 注册可声明字段 schema（UI 通用渲染）；可向已有 kind 追加字段；实例可写任意未声明字段（buff 等临时效果） |
| D10 | 实体分类标签 | kind 可挂 categories 标签；分类字段（add_category_fields）使该分类全部 kind 获得字段；list_kinds(category) 精准选取（如"给所有生物加血量"） |
| D11 | 移动与内核能力覆盖 | **移动收束内核**（默认 = 路径移动：读 connections → 抽目标）；**全部原语可被玩法包 override/disable**（每原语至多一个覆盖者，第二个报错；禁用后调用报错）；覆盖状态管理页可见 |
| D12 | 交互变更表达 | **删除 effects 机制**（取代 V4 A1 双轨）：InteractionResult 仅 text + ui；交互 handler 命令式调用内核原语（set_data / move_entity 等，锁内重入 + 异常隔离，机制已验证）；变更通知由事件总线 + SSE 承担；v5 只有命令式一轨 |
| D13 | 旧数据与库形态 | **数据不保留**：v5 为开发阶段完全重构，空库重建——启动删旧库（含 -wal/-shm），无迁移、无备份、无检测分支，零历史债务；旧账号/地图/背包/日志全部清除 |

## 10. 与现行版关系

- **v5 = 开发阶段完全重构，不保留任何历史债务**：升级即空库重建（D13），
  旧数据（账号/地图/背包/日志）不保留；无迁移、无备份、无兼容分支
- v0.3.0 仅存于 git 历史（D4）供参考/复用，主线不含 v4 兼容代码
- 所有玩法包（内置+社区）面向同一套内核 API 与协议，无 v3/v4 之分
