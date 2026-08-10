<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD025 -->
<!-- markdownlint-disable MD041 -->
# ChangeLog

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
