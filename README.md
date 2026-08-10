<!-- markdownlint-disable MD041 -->

<div align="center">

<p><strong>简体中文</strong></p>

<h1>世界编辑器</h1>

<p><strong>为 AstrBot 构建的图结构世界：一个可以无限生长的有向图世界，AI 与人都能进入。</strong></p>

<p><sub>有向图底座 &nbsp;&nbsp; 无限生长 &nbsp;&nbsp; AI 与人共存 &nbsp;&nbsp; 实体与交互</sub></p>

<p>
  <a href="https://github.com/Rail1bc/astrbot_plugin_worlditor/releases"><img src="https://img.shields.io/badge/%E7%89%88%E6%9C%AC-v0.2.0-5f7f79?style=flat-square&labelColor=263a36" alt="最新版本"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-e9f1ef?style=flat-square&labelColor=263a36" alt="Python 3.12 或更高版本">
  <img src="https://img.shields.io/badge/AstrBot-%3E%3D%204.24.1-f3eee4?style=flat-square&labelColor=544c3d" alt="AstrBot 4.24.1 或更高版本">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-f2e8e5?style=flat-square&labelColor=5b403a" alt="AGPL-3.0 许可证"></a>
</p>

</div>

> [!NOTE]
> **当前版本状态**：v0.2.0 在"图结构世界"基础上加入**可视化编辑**与**玩家视图**——调试页单页双模式（管理员可视化编辑地图 / 模拟玩家体验迷路）。正式用户入口（独立网页、用户系统）仍在 v2 路线中。完整设计见 [DESIGN.md](DESIGN.md)。

## 这是什么

一个可以无限生长的世界：

- **世界 = 有向图**：地块（Location）是节点，带标签的出口（Exit）是有向边。`a→b` 可达**不蕴含** `b→a`；没有空间相邻，只有出边才构成"相邻/可达"。地图可以包含多边同目标、隐藏目标（`???`）与环路——图数据结构本身即允许抽象、高复杂度的结构。
- **实体与交互是内容**（长期愿景）：人物、生物、建筑、路障，乃至非现实物品皆为实体；交互方式可无限扩展，交互总有新花样。
- **AI 与人都能进入**：agent 是世界的常住居民，位置跨对话持久化；v1 人类玩家以隐形实体身份进入，v2 将引入账户体系。

<table>
<tr>
<td width="33%"><strong>有向图底座</strong><br><br>地块为节点、带标签出口为有向边；多边同目标 / 隐藏目标 / 环路，图本身即可表达高复杂度的抽象结构。</td>
<td width="33%"><strong>无限生长</strong><br><br>世界可不断扩展：增删地块与出口、引入实体与交互，内容由 AI 与玩家共同织就。</td>
<td width="33%"><strong>AI 与人共存</strong><br><br>agent 以固定身份住在世界里，位置跨对话持久化；玩家 v1 为隐形实体、v2 为账户化用户。</td>
</tr>
</table>

## 三步开始

1. 从 AstrBot 插件市场安装，或从 [Release](https://github.com/Rail1bc/astrbot_plugin_worlditor/releases) 下载 zip 在「插件」页安装并启用。
2. 重载 AstrBot——首次启动会播种示例小镇 + 迷雾区（agent 初始在广场）。
3. 与 agent 对话，让它探索这个世界——它会在需要时调用 `world_look` 查看场景、用 `world_move` 移动；管理员也可以在「插件 → 世界编辑器」调试页里直接操作地图。

## Agent 工具

- `world_look`：查看当前位置（地块 id / 名称 / 描述）与可移动的出口列表；隐藏目标显示 `???`。
- `world_move(exit_id)`：沿出口移动到新位置，并返回新位置的场景；非法出口返回中文错误串，LLM 可据此自纠。

场景以中文文本注入下一轮 prompt，例如：

```text
你当前位于：小镇广场（town_plaza）
描述：小镇的中心广场，人来人往。东西南北都有街道延伸出去。
可移动的出口：
  [town_plaza_cafe] 沿着东街走向咖啡店 → 街角咖啡店
  [town_plaza_library] 沿着北街走向图书馆 → 老图书馆
```

## 插件调试页（单页双模式）

供管理员在 dashboard 内验证与编辑世界（**非正式用户入口**，正式入口为 v2 独立网页）：

- **编辑模式**（上帝视角）：全图可视化 + 可视化编辑——点击节点 / 边进入表单，增删改地块与出口、设置出口方向（上 / 右 / 下 / 左）与「隐藏目的地」开关、调整布局坐标；单向 / 双向边以箭头区分，重名地块悬浮全体高亮。
- **玩家模式**：模拟玩家视角——只显示当前地块 + 有出边连接的 1 跳目标（十字布局），隐藏目标显示 `???`，点击目标格按 `exit_id` 移动。
- 无本地 player_id 时自动注册隐形玩家（仅内存，刷新即重新注册）。

## 数据与持久化

- 数据库：`data/plugin_data/astrbot_plugin_worlditor/world.db`（SQLite，WAL，启动全量载入内存）。
- agent 位置跨对话持久化；人类玩家仅内存，15 分钟无活动自动清理。
- 空白库自动播种示例小镇 + 迷雾区，演示多边同目标 / 隐藏目标 / 环路。

## 路线图

- **v2**：独立网页（移动端优先）+ 用户系统（注册 / 登录 / token，世界玩家与账户绑定）；暴露世界 HTTP API（共享 token + CORS），插件为唯一权威后端。
- **实体与交互系统**：人物 / 生物 / 建筑 / 物品等实体，对话 / 开启 / 破坏 / 阅读等交互，LLM 生成 NPC 对话。
- **地图可视化编辑**：增删地块、增删带标签有向出口、设置 `reveal_target` 与布局坐标——v0.2 已在调试页落地单页双模式的编辑能力，v2 独立网页将作为正式入口。
- **人与 agent 实时互见**：SSE 事件流广播全量快照。
- **MCP 封装**：引擎 action 层协议无关，后续抽独立进程 + FastMCP 薄封装。
- **v3**：独立应用。

详见 [DESIGN.md](DESIGN.md)。

## 开发者

fork 本仓库，在功能分支上修改并推送，然后向本仓库 `main` 分支发起 Pull Request——CI 会自动运行测试与格式检查。

```bash
python -m pytest tests/ -q          # 单元测试
python -m ruff check .              # 代码质量检查
python -m ruff format --check .     # 格式检查
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 项目

[更新记录](CHANGELOG.md) · [设计文档](DESIGN.md) · [贡献指南](CONTRIBUTING.md) · [版本发布](https://github.com/Rail1bc/astrbot_plugin_worlditor/releases) · [问题反馈](https://github.com/Rail1bc/astrbot_plugin_worlditor/issues)

世界编辑器使用 [AGPL-3.0 许可证](LICENSE) 发布。
