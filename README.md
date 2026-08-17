<!-- markdownlint-disable MD041 -->

<div align="center">

<p><strong>简体中文</strong></p>

<h1>世界编辑器（worlditor）</h1>

<p><strong>为 AstrBot 构建的世界平台内核：一个可以无限生长的世界，AI 与人都能进入。</strong></p>

<p><sub>地块 + 实体 &nbsp;&nbsp; 玩法包扩展 &nbsp;&nbsp; MCP 唯一动作通道 &nbsp;&nbsp; 独立 WebUI</sub></p>

<p>
  <a href="https://github.com/Rail1bc/astrbot_plugin_worlditor/releases"><img src="https://img.shields.io/badge/%E7%89%88%E6%9C%AC-v0.3.0-5f7f79?style=flat-square&labelColor=263a36" alt="最新版本"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-e9f1ef?style=flat-square&labelColor=263a36" alt="Python 3.12 或更高版本">
  <img src="https://img.shields.io/badge/AstrBot-%3E%3D%204.24.1-f3eee4?style=flat-square&labelColor=544c3d" alt="AstrBot 4.24.1 或更高版本">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-f2e8e5?style=flat-square&labelColor=5b403a" alt="AGPL-3.0 许可证"></a>
</p>

</div>

> [!NOTE]
> **当前版本状态**：v0.3.0（0.x 预发布，正式上线前保持 0 开头）——内核（worlditor）只提供世界的事实模型、动作原语、交互协议与玩法包注册表，**明确不做任何具体玩法**；玩法（角色养成、物品合成、交易市场、战斗……）由玩法包（`worlditor_play_*`）从开源社区生长。已落地：**MCP 唯一动作通道**（streamable HTTP + stdio，连接身份验证）、**身份自助注册**（open/invite/closed + token 三档）与**独立 WebUI**（移动端优先，WebUI 本身是 MCP 客户端，构建产物插件内置托管）。v3 的 agent 工具与调试页保留过渡。完整设计见 [DESIGN_V4.md](DESIGN_V4.md)。

## 这是什么

一个可以无限生长的世界平台：

- **世界只有两个概念：地块与实体**（v4，B12）。玩家（`kind=player`）、AI agent（`kind=agent`）是内置实体；NPC、门、告示牌、商贩都是玩法包注册的实体 kind——"饮料商人"与"自动售卖机"在功能上无本质区别，差异只是 kind 与文本。
- **内核只懂原语**：移动、说话、拾取、给/扣物品、触发交互、改状态、广播事件。不含伤害公式、合成配方、价格、升级曲线——这些都是玩法包的数据与代码。
- **玩法 = 玩法包**：物品、对话树、配方、货单是玩法包自带的数据文件；行为逻辑是玩法包代码。玩法包通过注册表接入内核（实体 kind / 交互动作 / 事件订阅 / UI 组件与钩子），与内核彻底解耦。
- **交互是协议**：`interact(实体, 目标, 动作)` → 玩法包 handler → 内核结算声明式 effects → 结构化结果 `{text, ui, effects}`。玩法包只描述界面，不画界面。
- **单一事件总线**：9 个事件（心跳 / 移动 / 进入 / 说话 / 交互 / 物品使用 / 实体移除 / 实体变化 / 世界编辑），玩法包订阅响应，同时写入世界日志。
- **AI 与人都能进入**：agent 与玩家都是身份化实体，有背包、位置持久化。

## 三步开始

1. 从 AstrBot 插件市场安装，或从 [Release](https://github.com/Rail1bc/astrbot_plugin_worlditor/releases) 下载 zip 在「插件」页安装并启用；在插件配置中开启 `enable_world_api`（世界 HTTP API / MCP + 内置 WebUI，默认端口 6288）。
2. 重载 AstrBot——首次启动会播种示例主世界（广场 · 步行街 · AstrBot大道 · 开源小区 · AstrBot大学 · 迷雾森林）与演示实体（商贩·阿福 / 告示牌 / 木门），并加载内置演示玩法包。
3. 浏览器打开 `http://<AstrBot主机>:6288/`（**插件内置托管**的 WebUI）→ 注册/登录（或"围观者身份"）→ 进入世界。

## WebUI（人类玩家入口，A2）

移动端优先前端（`webui/`，Vue3 + Vite），**本身是 MCP 客户端**（B10：与 agent 共用同一套工具与认证）：

- **世界**：触屏 SVG 网格地图（拖动/缩放）、方向按钮移动、说话、同地块角色条；点击实体 → 交互弹窗（按 UiBlock schema 渲染：text/menu/list/form/confirm/character；custom 块显示 fallback 文本）。
- **背包**：物品网格，点击使用（world_use）。
- **角色**：自己的实体属性/状态、修改密码。
- **日志**：SSE 实时事件流（说话/移动/交互/编辑），切换"全图/当前地块"。
- 实时性（B11）：SSE 事件驱动增量更新 + 断线重连拉快照兜底，不轮询。

**部署方式（二选一）**：

- **插件内置托管（默认，推荐）**：`webui/dist/` 构建产物随插件发行，6288 世界服务自动挂载为根路径——开启 `enable_world_api` 后访问 `http://<主机>:6288/` 即完整 WebUI，**无需单独部署前端**。
- **独立部署**：`npm run build` 产物交给任意静态服务器/反向代理，`VITE_WORLD_API` 指向世界服务；跨域时在后端 `allowed_origins` 加入该域名。

开发运行：`cd webui && npm install && npm run dev`（默认 5173；改前端后 `npm run build` 更新内置产物）。

## 接入（MCP 唯一动作通道，B10）

- **远程 agent（联邦基础）**：任意 MCP 客户端（AstrBot 的 MCP client 或任何标准客户端）配置世界服务地址 `https://world.example.com/world/mcp` + agent 凭据（`/auth/agent-register` 自助注册，或 `/auth/register` 带 `admin_key` 由管理员通道创建）即可加入世界，**无需安装 worlditor**。
- **本地 agent**：`python -m astrbot_plugin_worlditor.world.mcp.stdio --db <world.db> --token <凭据>`（stdio 配置）。
- 工具集：`world_look` / `world_move` / `world_say` / `world_bag` / `world_use` / `world_interact` / `world_who`，返回 `{text, ui, effects}` 结构化 JSON。
- 身份：token 三档（read 围观 / play 动作 / admin 管理），`auth_mode` 三模式（open / invite / closed），管理员凭 `admin_key` 注册；凭据可吊销/改密。

## 玩法包（开发者入口）

玩法包 = 一个目录（`worlditor_play_*` 前缀）+ `play.yaml` + `main.py`：

```
data/plugin_data/astrbot_plugin_worlditor/plays/   # 玩法包根目录（与框架插件目录隔离）
└── worlditor_play_dungeon/
    ├── play.yaml        # 元数据：name / version / requires（worlditor 版本）
    ├── main.py          # 入口：def setup(api, context) -> None
    ├── data/            # 物品 / 对话树 / 配方 / 货单（json/yaml）
    └── web/             # 前端资源：自定义界面组件（v4.1）
```

玩法包通过 `WorlditorPlayAPI` 与内核交互（唯一入口，`from astrbot_plugin_worlditor.api import WorlditorPlayAPI`）：

- 注册：`register_item_def` / `register_entity_kind` / `register_interaction` / `register_world_event` / `register_ui_component` / `register_ui_hook`
- 只读：`get_entity` / `list_entities` / `get_location` / `get_map` / `list_actions`
- 数据：`kv_get` / `kv_set`（namespace 自动 = 玩法包 id）
- 动作：`give_item` / `take_item` / `count_item` / `list_inventory` / `move_entity` / `set_attrs` / `get_attrs` / `set_state` / `get_state` / `say` / `interact`

内核自带 `demo_play/`（随发行，可删除）：演示 item / entity_kind / interaction / event 完整链路，充当 SDK 模板。

## Agent 工具（v3，过渡期保留）

- `world_look`：查看当前位置与可移动方向（v3 引擎，位置跨对话持久化）。
- `world_move(direction, path=None)`：沿方向移动。

agent 与玩家的正式动作通道统一为 MCP 世界工具（见「接入」），v3 工具与调试页在 v4.1 过渡期保留，后续版本移除。

## 数据与持久化

- 数据库：`data/plugin_data/astrbot_plugin_worlditor/world.db`（SQLite，WAL，启动全量载入内存）。
- v4 新增表：`entities` / `items` / `inventories` / `play_data` / `world_log`（上限 5000 条）；v3 表（maps/locations/templates/world_meta）结构沿用、数据共享。
- 空白库自动播种示例世界；v4 为破坏性重构但**无迁移**（旧 v3 设计数据直接丢弃重建，v4 表与 v3 表共存互不干扰）。

## 路线图

- **v4.0 底子内核（✅）**：v4 数据模型 / 物品与实体放置原语 / 交互与 effects 结算 / 玩法包加载器 + WorlditorPlayAPI / 广播道具与冷却 / 事件总线 / 种子世界 v4 + demo_play / 全套单测。
- **v4.1 独立 WebUI + MCP（✅ 本版）**：进程内 MCP server（streamable HTTP + stdio，连接身份验证）+ Vue3 移动端优先 WebUI（WebUI 本身是 MCP 客户端）+ 交互弹窗（角色卡/UiBlock 渲染）+ 玩法包界面扩展（ui_hook 三位置已落地；custom 组件动态加载 v4.2）+ SSE 实时 + token 三档 + 自助注册（open/invite/closed）。
- **v4.2 玩法 SDK 定型**：开发者文档（docs/PLAY_DEV.md）+ 玩法包依赖解析 + custom 组件动态加载 + 社区参考玩法。
- **v5 联邦**：MCP 公网通道，远程 AstrBot（无需安装 worlditor）经 agent 凭据加入同一世界。

详见 [DESIGN_V4.md](DESIGN_V4.md)（v4 设计）与 [DESIGN.md](DESIGN.md)（v3 现状）。

## 开发者

fork 本仓库，在功能分支上修改并推送，然后向本仓库 `main` 分支发起 Pull Request——CI 会自动运行测试与格式检查。

```bash
python -m pytest tests/ -q          # 单元测试
python -m ruff check .              # 代码质量检查
python -m ruff format --check .     # 格式检查
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 项目

[更新记录](CHANGELOG.md) · [设计文档](DESIGN_V4.md) · [贡献指南](CONTRIBUTING.md) · [版本发布](https://github.com/Rail1bc/astrbot_plugin_worlditor/releases) · [问题反馈](https://github.com/Rail1bc/astrbot_plugin_worlditor/issues)

世界编辑器使用 [AGPL-3.0 许可证](LICENSE) 发布。
