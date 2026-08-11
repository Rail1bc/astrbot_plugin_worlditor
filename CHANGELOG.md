<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD025 -->
<!-- markdownlint-disable MD041 -->
# ChangeLog

## [Unreleased] - 2026-08-11

### 🛠 玩家视图重构

- **玩家视图布局重构**：地图 div 中间是只含地块名称的小块（移除说明文本与玩家 id），上下左右直接连接 1 跳可达地块；当前地块说明文本移到与地图 div 平级的独立信息 div（实体系统后续版本再接入）。
- **编辑防冲突**：新建出口时方向默认选「出发地块的第一个空闲方向」，切换出发地块若方向冲突自动改选，避免无意中造出同方向冲突（减少「+N」折叠的误触发）。

## [v0.2.0] - 2026-08-10

### 🎮 可视化编辑与玩家视图（Milestone）

- **出口方向槽位**：`Exit` 新增 `direction` 字段（`up/right/down/left` 之一），同出发地块的出边方向互异——多边同目标分占不同十字槽位、不重合；老库自动迁移（`ALTER TABLE`），schema 升 v2。
- **地图可视化编辑**：引擎新增 6 个编辑动作（地块 / 出口的创建、更新、删除）；删除地块级联删除相关出边、拒绝删除有玩家（含 agent）占据的地块；自环出口合法；布局坐标支持显式清空。
- **Web API 编辑端点**：新增 `POST /world/location/{create,update,delete}` 与 `POST /world/exit/{create,update,delete}`。
- **调试页重构为单页双模式**（模块化拆分为 5 个 ES module）：
  - **编辑模式**：全图可视化（上帝视角，地块永远真名）+ 可视化编辑——点击节点 / 边进入表单，设置名称、描述、布局坐标、出口方向与「隐藏目的地」开关；边画方向信息（单向箭头 / 双向双箭头）；重名地块悬浮全体高亮。
  - **玩家模式**：当前地块 + 有出边连接的 1 跳目标十字布局——无连接的槽位不可见；所有边一视同仁（无箭头简单连线，不区分单向 / 双向）；无回环（自环出口照常占槽位）；隐藏目标显示 `???`；违规地图（出度 >4 或同方向冲突）以「+N」折叠兜底。
- **约束分层**：数据层不设出度限制（合法 = 结构允许），规范由可视化编辑器保证，视图层以「+N」折叠兜底违规地图。
- CI js-check 覆盖全部 5 个前端模块。

## [v0.1.0] - 2026-08-10

### 🎉 初始版本（Milestone）

- **有向图世界底座**：地块（Location）为节点、带标签出口（Exit）为有向边——`a→b` 可达不蕴含 `b→a`；支持多边同目标、隐藏目标（`reveal_target=False`，场景显示 `???`）与环路，图数据结构允许抽象、高复杂度的结构。
- **LLM 工具**：agent 化身经 `world_look` / `world_move` 查看场景与移动，位置跨对话持久化（SQLite）。
- **框架内置调试页**（`pages/world/`）：SVG 有向图可视化（布局坐标优先、确定性兜底布局）、按出口 id 移动、当前地块高亮与 agent 静态标记。
- **Web API**：`GET /world/state`、`POST /world/player/register`、`POST /world/move`、`POST /world/player/deregister`。
- **持久化**：SQLite（aiosqlite + WAL，启动全量载入内存）；空库幂等播种示例小镇 + 迷雾区（多边同目标 / 隐藏目标 / 环路示例）；agent 位置写回、人类玩家仅内存且超时清理。
- **协议无关引擎**：`world/` 纯 Python 引擎不依赖框架，动作层被 LLM 工具 / 插件页 API / 未来世界 HTTP API 共用，为 MCP 封装预留。
- **工程化**：CI 工作流（`pytest.yml` 单元测试 + 前端 JS 语法检查、`ruff-format.yml` 格式与质量检查）；插件名统一为「世界编辑器」。

### 📝 文档与工程化（Docs & Chores）

- 补齐 LICENSE（AGPL-3.0）、README、CONTRIBUTING、CODE_OF_CONDUCT、CHANGELOG、issue/PR 模板与 dependabot。
