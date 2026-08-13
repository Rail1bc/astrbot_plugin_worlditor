<!-- markdownlint-disable MD041 -->

<div align="center">

<p><strong>简体中文</strong></p>

<h1>世界编辑器</h1>

<p><strong>为 AstrBot 构建的网格世界：一个可以无限生长的世界，AI 与人都能进入。</strong></p>

<p><sub>网格底座 &nbsp;&nbsp; 4 方向连接 &nbsp;&nbsp; AI 与人共存 &nbsp;&nbsp; 实体与交互</sub></p>

<p>
  <a href="https://github.com/Rail1bc/astrbot_plugin_worlditor/releases"><img src="https://img.shields.io/badge/%E7%89%88%E6%9C%AC-v3.0.0-5f7f79?style=flat-square&labelColor=263a36" alt="最新版本"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-e9f1ef?style=flat-square&labelColor=263a36" alt="Python 3.12 或更高版本">
  <img src="https://img.shields.io/badge/AstrBot-%3E%3D%204.24.1-f3eee4?style=flat-square&labelColor=544c3d" alt="AstrBot 4.24.1 或更高版本">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-f2e8e5?style=flat-square&labelColor=5b403a" alt="AGPL-3.0 许可证"></a>
</p>

</div>

> [!NOTE]
> **当前版本状态**：v3.0.0 将世界底座重构为**网格世界**——地块以 (行, 列) 为身份落在网格上，每地块固定 4 个方向槽位、槽内多条**平行路径**（每条路径有分时段加权文本标签、可隐藏目的地、可携带多个加权意外目标）；引入**地块模板**（复制预设）。`world_move` 从按出口 id 改为按「方向 + 路径索引」。v3 为**破坏性变更且无迁移**：solo 迭代、未公开，旧 world.db 直接丢弃、播种世界重建。完整设计见 [DESIGN.md](DESIGN.md)。

## 这是什么

一个可以无限生长的世界：

- **世界 = 网格 + 4 方向连接**：地块（Location）以 (行, 列) 为身份落在网格上，每个地块固定 4 个方向槽位（上/右/下/左），槽位内可配置多条**平行可选路径**——每条路径带分时段加权文本标签、可选隐藏目的地（`???`）与多个加权意外目标（如「脚滑跌下悬崖」「没看路掉进井盖」）。`a→b` 可达**不蕴含** `b→a`；隐藏目标 / 环路 / 平行路径表达的复杂度由路径结构承载。
- **实体与交互是内容**（长期愿景）：人物、生物、建筑、路障，乃至非现实物品皆为实体；交互方式可无限扩展，交互总有新花样。
- **AI 与人都能进入**：agent 是世界的常住居民，位置跨对话持久化；v1 人类玩家以隐形实体身份进入，v2 将引入账户体系。

<table>
<tr>
<td width="33%"><strong>网格世界</strong><br><br>地块以 (行, 列) 定位，每地块固定 4 方向槽位；槽内平行路径 × 多目标，隐藏 / 环路 / 意外事件皆可表达。</td>
<td width="33%"><strong>无限生长</strong><br><br>世界可不断扩展：增删地块与连接路径、引入实体与交互，内容由 AI 与玩家共同织就。</td>
<td width="33%"><strong>AI 与人共存</strong><br><br>agent 以固定身份住在世界里，位置跨对话持久化；玩家 v1 为隐形实体、v2 为账户化用户。</td>
</tr>
</table>

## 三步开始

1. 从 AstrBot 插件市场安装，或从 [Release](https://github.com/Rail1bc/astrbot_plugin_worlditor/releases) 下载 zip 在「插件」页安装并启用。
2. 重载 AstrBot——首次启动会播种示例主世界：广场 · 步行街 · AstrBot大道（两侧商铺）· 开源小区 · AstrBot大学 · 迷雾森林（agent 初始在广场）。
3. 与 agent 对话，让它探索这个世界——它会在需要时调用 `world_look` 查看场景、用 `world_move` 移动；管理员也可以在「插件 → 世界编辑器」调试页里直接操作地图。

## Agent 工具

- `world_look`：查看当前位置（地块名称 / 描述）与 4 个方向的连接槽位——每个方向逐条列出平行路径（分时段文本标签 + 主目标名；隐藏目标显示 `???`）。
- `world_move(direction, path=None)`：沿方向移动，多条平行路径时用 `path` 指定路径索引（索引来自 `world_look`，每次重列）；路径内若含多个加权意外目标则按权重随机命中。非法方向 / 路径返回中文错误串，LLM 可据此自纠。

场景以中文文本注入下一轮 prompt，例如：

```text
你当前位于：小镇广场
描述：阳光洒在广场中央的喷泉上，水花晶莹，人来人往。
可移动的方向：
  up[0] 前往步行街·南街口 → 步行街·南街口
  right[0] 前往AstrBot大道 → AstrBot大道
  down[0] 前往老路 → 老路
  left[0] 前往AstrBot大道 → AstrBot大道
```

## 插件调试页（单页双模式）

供管理员在 dashboard 内验证与编辑世界（**非正式用户入口**，正式入口为 v2 独立网页）：

- **编辑模式**（上帝视角，网格地图 + 右键拖动 + 缩放 + 缩略图 + 详情栏）：地图为**固定大小网格**——地块按 (行, 列) 落在主格（正方形，只显示名字 + 出生点徽标；**坐标只读**，移动走专门工具），**连接绘制在地块间隙中的 SVG 连线**——每个方向的连接槽位（上/右/下/左）画在对应一侧间隙，槽内多条平行路径沿间隙垂直方向错开（主路径实线、意外路径以目标点呈现；死引用 = 启用但主目标不可解析 → 红色虚线）；点击间隙可编辑**两侧地块**对应方向的槽位（路径增删 / 排序 / 权重 / 隐藏目标）；**查看 / 编辑子模式**：查看模式只显示已存在的地块与连接，编辑模式显示全部空地块（点击新建，可选模板）与网格背景；**视图隐藏横竖滚动条**：右键拖动 / 滚轮平移，Ctrl/⌘+滚轮以光标为中心缩放，工具条提供 − / + / 百分比 / 适应；**右下角全图缩略图**：显示全图与当前视口范围，可收起/展开、可拖动，点击或拖动可跳转视口；右侧详情栏可收起/展开——点击地块查看/编辑（名称 / 分时段描述 / 槽位摘要 / 「移动地块」工具 / 捕获为模板 / 删除）、点击间隙编辑连接、点击空地块新建。
- **玩家模式**：模拟玩家视角——地图中间是只含地块名称的小块，上/右/下/左各放一个**方向槽位格**（无路径的方向不渲染），格内是该方向**全部平行路径的按钮列表**（每条显示文本标签 + 主目标名；隐藏目标显示 `???`）；当前地块说明文本在平级详情区（PC 左侧地图、右侧详情一整列；移动端上地图、下详情，详情列单独滚动）；点击路径按钮按「方向 + 路径索引」移动。
- 无本地 player_id 时自动注册隐形玩家（仅内存，刷新即重新注册）。

## 数据与持久化

- 数据库：`data/plugin_data/astrbot_plugin_worlditor/world.db`（SQLite，WAL，启动全量载入内存）。
- agent 位置跨对话持久化；人类玩家仅内存，15 分钟无活动自动清理。
- 空白库自动播种示例主世界（广场 · 步行街 · AstrBot大道 · 开源小区 · AstrBot大学 · 迷雾森林）：相邻地块默认双向连接，森林地块无路方向均通向「迷雾深处」。**v3 为破坏性变更且无迁移**：旧 v2 库数据直接丢弃，播种世界重建。

## 路线图

- **v2 正式入口**：独立网页（移动端优先）+ 用户系统（注册 / 登录 / token，世界玩家与账户绑定）；暴露世界 HTTP API（共享 token + CORS），插件为唯一权威后端。
- **实体与交互系统**：人物 / 生物 / 建筑 / 物品等实体，对话 / 开启 / 破坏 / 阅读等交互，LLM 生成 NPC 对话。
- **地图可视化编辑**：v3 已按新模型（网格 + 4 方向槽位 + 平行路径）在调试页落地；v2 独立网页将作为正式的可视化编辑入口。
- **人与 agent 实时互见**：SSE 事件流广播全量快照。
- **MCP 封装**：引擎 action 层协议无关，后续抽独立进程 + FastMCP 薄封装。
- **独立应用**：独立仓库的方向预留（移动客户端 / 更完整形态），最后考虑。

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
