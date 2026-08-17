# worlditor 重构设计 v5（定稿）——项目重开：内核纯数据，玩法包承载一切

> 状态：**设计定稿**（2026-08，D1–D7 已确认）。基于 v0.3.0 实际运行经验与多轮
> 讨论重开设计。配套：DESIGN_V4.md（现行架构，v0.3.0 已实现）。v5 为**项目重开**：
> 保留有价值的代码（复制复用），行为层整体以"玩法包"身份重写。
> 本文件是唯一权威设计文档。

## 0. 一句话定位

**插件 = 世界的"数据 + 编辑 + 身份 + 传输 + 玩法包管理"平台；一切行为、规则、
工具与界面由玩法包提供。** 插件内置多个领域玩法包（可停用/替换/删除）确保开箱
可玩，兼作 SDK 模板。

- 内核不做任何玩法判断：不知道"玩家怎么移动、视野多大、怎么说话、页面长什么样"
- 内核保证：数据永远正确、身份永远可验、扩展永远有入口、管理永远在手

## 1. 架构

```
玩法包 worlditor_play_*（内置领域包 ×5 + 社区包）
  ├─ 行为：on_tick 状态机 / 事件订阅 / 交互 handler / 自定义事件
  ├─ 规则：移动 / 视野 / 广播 —— 读数据 → 调内核原语（规则可整体替换）
  ├─ 工具：register_tool 注册 MCP 工具（身份经 api.caller() 获取，内核裁定权限）
  └─ 视图：register_view 注册页面（UiBlock 协议，WebUI 渲染）
        │  注册表 API（唯一入口，锁内执行，异常/namespace 隔离）
        ▼
内核 worlditor（v5）
  ├─ 事实层：地块/连接/模板 + 实体/物品/背包/日志（SQLite WAL，全量内存快照）
  ├─ 原语：place_entity / remove_entity / move_entity / set_attrs / set_state /
  │         give_item / take_item / count_item / interact（effects 内核结算）
  │         （无 say——说话能力全部下沉玩法包，D1）
  ├─ 身份：账户 / token 三档 / auth_mode / 邀请码 / 吊销（不可下沉）
  ├─ 编辑：地块/连接/地图/实体数据编辑（admin 权限，含多地图）
  ├─ 管理：玩法包 list / enable / disable / uninstall + 状态持久化（内置能力）
  ├─ 传输：MCP（streamable HTTP + stdio，连接即身份验证）+
  │        REST 非动作端点 + SSE + 内置 WebUI（玩法包视图宿主）
  └─ 协议：UiBlock / InteractionResult / 事件总线（基础设施：订阅/分发/日志/
            SSE 序列化支持任意事件名；事件语义由玩法包定义）
```

## 2. 内核红线（不可下沉清单）

| 红线 | 理由 |
|---|---|
| 事实模型与持久化（全部表 + 并发锁 + 级联清理） | 数据正确性：玩法包再怎么写，背包账目不会错 |
| 身份与凭据（token→实体、auth_mode、三档权限、吊销） | 安全：工具由玩法包注册后，"调用者是谁"仍由内核裁定 |
| 基础原语（place/remove/move_entity/set_attrs/set_state/give/take/count/interact，**无 say**） | 行为层地基：玩法包移动 = 读连接 → 调 move_entity；说话 = 玩法包 emit 事件（D1） |
| 事件总线基础设施（订阅/分发/异常隔离/日志） | 机制留内核，事件语义与自定义事件开放给玩法包 |
| MCP 传输层 + 连接认证 + 身份注入 | 通道留内核，工具内容玩法包注册 |
| 地图编辑权限与编辑原语 | 世界内容治理（admin） |
| UI 渲染协议（UiBlock schema） | 协议留内核，视图内容玩法包提供 |
| **玩法包管理**（list/enable/disable/uninstall） | 管理"扩展机制"本身，天然属于内核 admin 域 |

## 3. 玩法包体系

### 3.1 内置领域包（5 个，随内核发行、自动加载、可停用/替换/删除）

| 玩法包 | 领域 | 贡献 |
|---|---|---|
| `worlditor_play_items` | 物品与背包 | 物品 use 规则、背包视图、world_bag/world_use 工具 |
| `worlditor_play_player` | 玩家 | 玩家实体行为、出生礼包、角色视图 |
| `worlditor_play_movement` | 移动与视野 | **移动规则**（读 connections → 选目标 → move_entity）、3×3 视野视图、world_look/world_move/world_who 工具 |
| `worlditor_play_interaction` | 交互 | 交互弹窗编排、动作菜单、world_interact 工具 |
| `worlditor_play_social` | 说话与广播 | **说话规则（D1：内核无 say）**：cell 级说话、world 级广播（喇叭消耗 + 冷却，全部玩法化，用 items 数据 + kv/attrs 自管）、world_say 工具、日志视图 |

**协作模型：事件驱动，零包间调用**——移动包更新位置 → 内核发 `on_entity_move`
→ 视野/日志包各自订阅刷新。包只依赖内核数据与事件，不依赖其他包存在与否；
删除任何包世界照常运行（只是少对应能力）。加载顺序只影响"能力何时可用"，
不影响正确性（事件在包加载后订阅，错过的事件由快照兜底）。

### 3.2 玩法包管理（内核内置，admin）

| 能力 | 说明 |
|---|---|
| list | 名称/版本/作者/desc/requires/**状态**（loaded/disabled/加载失败+错误详情） |
| enable / disable | 即时生效：复用 load_one / teardown / clear_play_registrations |
| uninstall | 删除社区包目录（内置包仅可停用） |
| 状态持久化 | enabled 标记落库，重启按标记加载（内置包默认启用） |
| 整体重载 | 随内核（C2 保持：不做代码热重载） |

入口：admin REST 端点 + WebUI 管理视图（admin 档可见）+ 可选 MCP admin 工具。

### 3.3 玩法包基础设施（内核新增，前置条件）

| 能力 | 说明 |
|---|---|
| plays 依赖解析 | `requires.plays` 加载顺序保证（社区包可声明依赖领域包） |
| MCP 动态工具 | `api.register_tool(name, schema, handler)`；handler 经 `api.caller()` 拿当前身份，权限按 token 档位裁定；同名工具冲突策略见决策 D2 |
| 自定义事件 | `api.emit(event, **data)` + 任意事件名订阅；SSE 序列化/world_log 通用化——**说话下沉的通道**（D1：social 包 emit on_say 语义事件） |
| 视图宿主 | `api.register_view(key, {title, icon, provider})`；WebUI 渲染玩法包视图（世界页=movement、背包=items、角色=player、日志=social；社区可加新页） |
| WorlditorPlayAPI 增补 | +register_tool / +emit / +register_view / +caller |

## 4. 项目重开形态（决策 D4，待确认）

建议：**同一仓库重开**——git 历史保留（v0.3.0 随时可 checkout 复用），代码结构
按新架构重排，版本线重新规划（建议从 v0.4.0 继续，或重置 v0.1.0，见 D4）。
不新建仓库（复制复用成本最低，历史对比方便）。

## 5. 复用清单（从 v0.3.0 复制，已验证可用）

| 来源 | 去向 | 说明 |
|---|---|---|
| `world/v3model.py` | 原样 | 地块/连接/TextSchedule/模板——数据模型稳定 |
| `world/store.py` + `world/v4store.py` | 原样 | 全部表结构（maps/locations/templates/world_meta + entities/items/inventories/play_data/world_log + accounts/tokens/invite_codes） |
| `world/v4engine.py` 机制部分 | 复制删减 | move_entity / interact+effects / set_attrs / set_state / give/take/count / 事件总线（**开放任意事件名 + emit**）/ 地图编辑原语 / 订阅；**删除**：move（路径移动）、say（含喇叭与冷却，D1）、7 工具注册 |
| `world/identity.py` | 原样 | 身份红线 |
| `world/mcp/http.py` + `stdio.py` | 原样删减 | 传输/认证/世界服务端点（/auth /state /scene /bag /events /admin）；**删除**：内置 7 工具注册（改动态）；/events 出口支持玩法包自定义事件通用 payload |
| `api/` | 复制 | admin 端点（地图编辑含实体放置）等非动作端点 |
| `pages/world/*`（v3 编辑页） | 原样 | 编辑能力 UI（补多地图支持） |
| `webui/` 框架 | 复制改造 | App/路由/登录/token/MCP client/store/样式 → 视图宿主；页面内容改由玩法包视图提供 |
| `demo_play/` | 决策 D3 | 建议删除（领域包即模板） |
| 测试 | 迁移 | 数据层/身份/传输测试保留；行为测试迁到各领域包 |

## 6. 分阶段路线（每阶段可独立发布/验证）

```
M1 玩法包基础设施：管理（list/enable/disable/uninstall+持久化）、plays 依赖解析、
   MCP 动态工具、自定义事件、视图宿主 —— 内核能力"开放"，默认行为仍在内核（向后兼容）
M2 内核瘦身：删除 move/say/7 工具/默认页面内容；WebUI 转视图宿主
M3 领域包逐个落地：items → player → movement → interaction → social（每包独立可测）
M4 验证与收尾：一个"替代玩法包"（如同方向延伸视野+朝向）证明可替换；
   删除全部内置包的世界仍可编辑/浏览（管理页可见空态）；测试迁移完成；文档
   docs/PLAY_DEV.md；版本发布
```

## 7. 决策记录（已全部确认）

| # | 决策点 | 结论 |
|---|---|---|
| D1 | 说话能力归属 | **内核无 say**：单地块说话与全图喇叭广播全部下沉玩法包（social 包实现规则：cell 说话、world 广播消耗喇叭+冷却，全部玩法化；通道 = 自定义事件 emit + items 数据） |
| D2 | 同名工具冲突 | **报错并拒绝注册**（管理页可见错误，避免静默替换） |
| D3 | demo_play | **删除**（领域包兼作 SDK 模板，避免双份维护） |
| D4 | 重开形态 | **同仓库重开，版本继续 v0.4.0**（git 历史保留，旧代码可 checkout 复用） |
| D5 | 内置包默认状态 | **默认全部启用**，管理页可停用（停用有"将失去对应能力"提示） |
| D6 | 移动规则归属 | 移动 = movement 包注册的规则（读 connections → 选目标 → move_entity），内核无默认 |
| D7 | 视图宿主形态 | WebUI 仅渲染玩法包视图；内核保留登录、token、背包数据通道与兜底"无视图"提示 |

## 8. 与现行版关系

- v0.3.0 继续可用（git 历史）；重构完成前不破坏
- 重构完成后 v5 成为主线（新版本号），旧代码仅历史参考
- 所有玩法包（内置+社区）面向同一套内核 API 与协议，无 v3/v4 之分
