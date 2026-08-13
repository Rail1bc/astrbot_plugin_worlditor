# 世界编辑器（astrbot_plugin_worlditor）设计文档

## 定位

一个可以无限生长的世界：网格为底座，实体与交互为核心内容，AI 与人都能进入。

- **世界 = 网格 + 4 方向连接**：地块（Location）以 (map_id, 行, 列) 为身份；连接内嵌于地块的固定 4 方向槽位，每槽多条平行路径。`a→b` 可达**不蕴含** `b→a`；隐藏目标 / 环路 / 平行路径与意外目标的复杂度由路径结构承载。
- **实体与交互是内容**（长期愿景）：实体横跨人物、生物与非生物——建筑、路障，乃至非现实物品；交互方式可无限扩展，交互总有新花样。
- **引擎协议无关**：动作层被 LLM 工具 / 插件页 API / 未来世界 HTTP API 共用，为 MCP 封装预留。
- **v3 现状**：网格地图 + 4 方向槽位连接（平行路径 / 多目标加权 / 分时段文本 / 模板）；人类玩家为隐形实体（仅内存），agent 有 `world_look` / `world_move` 工具且位置跨对话持久化；框架内置插件页仅作调试。

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

## 数据模型（world/v3model.py，v3 现状）

v2 模型（id 关联的有向图 Location/Exit）已删除，直接替换为 v3：**地块身份 = (map_id, 行, 列)**；连接内嵌于地块的**固定 4 方向槽位**（`ConnectionSlot`），每槽多条**平行路径**（`ConnectionPath`：分时段加权 label + `reveal_target` + `targets` 有序列表，首个 = 主目标、其余 = 意外加权目标）；文本用 **TextSchedule**（分时段加权，描述 / label / 地图描述复用）；**地块模板**为复制预设。完整模型定义与语义见下文「数据模型 v3（2026-08-13 已实施）」。

- `Player(player_id, name, map_id, row, col, is_agent=False, last_active_ts=0.0, user_id=None)` — 人类（v1）仅内存；agent 固定 `player_id="agent"` 位置持久化；`user_id` 为 v2 用户系统预留。
- `SceneView`（场景快照）：所在地块 + 已解析描述 + 可用路径列表（每条 = 方向 + 槽内路径索引 + 时段文本 label + 主目标名或 `???`；死引用已剔除）。

"迷路"效果由模型本身实现：平行路径 / 多目标加权（意外事件）/ 隐藏目标 / 环路。**约束分层**：方向槽固定 4 个（方向互异升格为结构约束），但同方向允许多条平行路径、路径内多目标——总出度不限（4 × 多路径 × 多目标），规范由可视化编辑器保证。

## 引擎动作（world/engine.py，协议无关）

全部变更在实例 `asyncio.Lock` 内；读路径走内存快照、免锁。时钟 / PRNG 注入（`WorldEngine(store, clock=None, rand=None)`），保证测试确定性。

- `await engine.describe_scene(player_id) -> SceneView | None` — 时间感知描述（TextSchedule 按时段 + 权重解析）+ 4 方向槽位可用路径（死引用剔除、只显示主目标名或 `???`）。
- `await engine.move(player_id, direction, *, path=None, target=None) -> SceneView` — 校验顺序：玩家存在 → 方向合法 → 该方向有可用路径 → 单路径省略 / 多路径必须给 `path` 索引 → 目标抽取（未显式 `target` 时在选中路径 targets 内按权重抽取；显式 `target` 须为路径目标之一）→ 更新位置 → agent 写回 SQLite；失败抛 `WorldError`（消息可直接展示）。
- `await engine.register_player(player_id, name=None, *, is_agent=False, user_id=None) -> Location`（幂等，放到默认地图出生点）
- `await engine.deregister_player(player_id) -> bool`（agent 不可注销）
- `await engine.touch(player_id) -> bool`（刷新活跃时间）
- 地图编辑（全部锁内；`_UNSET = object()` 哨兵区分「参数未提供=不变」与「显式传 None=清空」）：
  - `await engine.create_location(map_id, row, col, name, *, description=None, template_id=None) -> Location` — 重复坐标报错；`template_id` 给出时以模板为蓝本（显式 `name` 覆盖模板名）
  - `await engine.update_location(map_id, row, col, *, name=_UNSET, description=_UNSET) -> Location` — **坐标只读**；`description=None` 显式清空
  - `await engine.delete_location(map_id, row, col) -> None` — 级联：主目标指向被删地块 → 整条路径移除；意外目标 → 仅移除该目标；拒绝删除有玩家（含 agent）占据的地块
  - `await engine.move_location(map_id, row, col, to_row, to_col) -> Location` — 移动地块：原子重写自身坐标 + 全图指向旧坐标的连接目标（含自环）+ 该地块上玩家位置；目标格被占 → 拒绝（不做交换）
  - `await engine.update_connection(map_id, row, col, direction, *, enabled=_UNSET, paths=_UNSET) -> Location` — 方向不可改；`paths` 整体替换（每条含 label / reveal_target / targets）
  - 模板：`create_template(template_id, name, *, map_id, row, col)` / `update_template(template_id, *, name=_UNSET, map_id=_UNSET, row=_UNSET, col=_UNSET)` / `delete_template(template_id)` / `apply_template(template_id, *, map_id, row, col)`
  - 校验（结构合法，不设出度限制）：坐标整数（排除 bool）/ 合法范围、name 去空格非空、direction 在 `DIRECTIONS` 内；自环（指向自身的目标）合法
- 只读：`list_maps()` / `get_map(id)` / `list_locations()` / `get_location(map_id, row, col)` / `list_templates()` / `get_template(id)` / `get_player(id)` / `list_players()`
- 后台任务：`initialize()` 启动、`terminate()` 取消；每 60s 清理超 15 分钟无活动的非 agent 玩家。

`scene_to_text(scene) -> str`：场景渲染为中文文本（LLM 工具注入下一轮 prompt 的形态）。

## 持久化（world/store.py）

数据库：`data/plugin_data/astrbot_plugin_worlditor/world.db`，schema v3

| 表 | 说明 |
|---|---|
| `maps(id TEXT PK, name, description_json, timezone, spawn_row, spawn_col)` | 地图（本次仅 1 行默认地图）；`description_json` / `timezone` 可空 |
| `locations(map_id TEXT, row INT, col INT, name, description_json, conns_json, PRIMARY KEY(map_id,row,col))` | 地块；`conns_json` 存 4 槽位配置（纯文本 JSON） |
| `templates(id TEXT PK, name, data_json)` | 地块模板（复制预设） |
| `world_meta(key TEXT PK, value)` | `schema_version` + agent 位置 `(map_id,row,col)` |

- WAL；启动全量载入内存（读路径快）。
- `maps` 为空时幂等播种默认地图 + 种子地块（广场 · 步行街 · AstrBot大道 · 开源小区 · AstrBot大学 · 迷雾森林；相邻地块默认双向连接，森林无路方向 → 迷雾深处），agent 初始在出生点。
- **无 v2→v3 迁移**：solo 迭代、未公开，旧库（v2 `locations` / `exits` 表）数据直接丢弃，空库按新模型重建（见「数据模型 v3」一节）。
- 编辑写操作（`save_location` / `delete_location` / `save_template` / `delete_template` / `save_agent_pos`）遵循 DB 先、内存后的约定，同时维护 `loc_by_pos[(map_id,row,col)]` 与 `templates` 索引。

## LLM 工具（main.py）

- `world_look` — 当前地块（名称/描述）+ 4 方向槽位，每条平行路径以 `[方向:路径索引]` 列出（label 取时段文本 + 主目标名）；隐藏目标显示 `???`。
- `world_move(direction: str, path: int | None = None)` — 按方向移动（多路径时带路径索引）；docstring 参数必须带类型注解（参数缺类型注解 import 即 ValueError）；校验失败返回中文错误串让 LLM 自纠，不抛异常。

## Web API（api/，插件页）

经 `context.register_web_api(f"/{PLUGIN_NAME}{path}", ...)` 注册，`/api/plug/astrbot_plugin_worlditor/...` 分发：

| 端点 | 说明 |
|---|---|
| `GET /world/state?player_id=...` | 地图信息（`maps`）+ 全量地块（`locations`，含连接槽位）+ `templates` + 该玩家场景 + agent 位置 + 出生点（默认地图 spawn，被删则回落第一张地图） |
| `POST /world/player/register` `{name?}` | 随机 player_id（uuid4 前 8 位）、默认名 `旅行者-XXXX`、放出生点；返回 `{player_id, map_id, row, col, location_name}` |
| `POST /world/move` `{player_id, direction, path?, target?}` | 按方向移动（多路径时 `path` 为槽内路径索引；`target` 可选显式指定坐标，须为路径目标之一）并返回新场景；非法方向 / 路径 → 400 |
| `POST /world/player/deregister` `{player_id}` | 页面 unload 尽力注销（超时清理兜底） |
| `POST /world/location/create` `{map_id?, row, col, name?, description?, template_id?}` | 新建地块（重复坐标报错；可指定模板）；`description` 为字符串 / 时段对象 / null |
| `POST /world/location/update` `{map_id?, row, col, name?, description?}` | 更新地块属性（**坐标只读**）；缺省键不变，`description: null` = 清空 |
| `POST /world/location/move` `{map_id?, row, col, to_row, to_col}` | 移动地块（原子重写全图引用；目标格被占 → 400） |
| `POST /world/location/delete` `{map_id?, row, col}` | 删除地块（级联清空指向它的目标，拒绝删除有玩家占据的地块） |
| `POST /world/connection/update` `{map_id?, row, col, direction, enabled?, paths?}` | 编辑连接槽位（方向不可改；`paths` = 平行路径列表，每条含 label / reveal_target / targets） |
| `POST /world/template/create` `{id, name, map_id?, row, col}` | 从源地块捕获模板 |
| `POST /world/template/update` `{id, name?, map_id?, row?, col?}` | 改名或重新捕获 |
| `POST /world/template/delete` `{id}` | 删除模板 |
| `POST /world/template/apply` `{id, map_id?, row, col}` | 应用模板到空地块 |

handler 只做类型校验（dict、字符串、整数坐标排除 bool、布尔），语义校验抛给引擎（`WorldError` → 400 error 信封）；update 按 payload 出现的键拼 kwargs。

## 插件网页（pages/world/）— 单页双模式调试工具

定位：供管理员在 dashboard 内验证世界与移动逻辑，非正式用户入口（正式入口为 v2 独立网页）。单页内分段控件切换两种视图（`app.js` / `shared.js` / `edit-view.js` / `edit-forms.js` / `play-view.js`，纯 ES module 无构建）。

- 无本地 player_id → 先注册 → `GET /world/state` → 渲染（`shared.js` 以 (row,col) 读地块、按 `DIR_OFFSETS`（与引擎对齐）判定死引用）。
- **编辑模式（上帝视角，网格地图 + 右键拖动 + 缩放 + 缩略图 + 详情栏）**：绝对定位画布渲染，地块按 (row,col) 落主格（正方形 `CELL=120`，只显示名字 + 出生点徽标；**坐标只读**）；**连接绘制在地块间隙（`GAP=46`）中的 SVG 连线**——每个方向的连接槽位画在对应一侧间隙，槽内多条平行路径沿间隙垂直方向错开（主路径实线 + 箭头；路径内意外目标以目标点呈现；**死引用** = 槽启用但主目标不可解析 → 红色虚线 + 红标记）；点击间隙可选中**两侧地块**对应方向的槽位。**查看 / 编辑子模式**：查看模式只显示已存在的地块与连接；编辑模式显示全部空地块（可点击新建，可选模板）与网格背景。**视图隐藏横竖滚动条**：右键拖动 / 滚轮平移（Shift+滚轮横向），Ctrl/⌘+滚轮（含触控板捏合）以光标为中心缩放 + 工具条 − / + / 百分比 / 适应（20%–400%，默认适应内容边界居中）；**右下角全图缩略图**：显示全图与当前视口范围，可收起/展开、可拖动，点击或拖动可跳转视口。**右侧详情栏**（可收起/展开）：点击地块查看/编辑（名称 / 分时段描述 / 槽位摘要 / 「移动地块」工具 / 捕获为模板 / 删除）、点击间隙编辑连接槽位（路径增删 / 排序 / 每条独立 label + 隐藏目标 + 目标列表排序与权重）、点击空地块新建（可指定模板）。
- **玩家模式**：地图 div 中间是**只含地块名称的小块**，上/右/下/左各放一个**方向槽位格**（无路径的方向不渲染），格内是该方向**全部平行路径的按钮列表**（每条显示 label + 主目标名；隐藏目标显示 `???`，名称只取 scene 的 `target_name`，绝不全图查名）；当前地块说明文本渲染在与地图 div **平级的独立信息 div**（实体系统后续版本接入）；**视口内整体不滚动**：页面 `body` 固定 `100dvh` 高度，棋盘正方形取地图区域宽/高较小值、随区域变化自适应，详情列在 PC 为右侧一整列、移动端为下方固定高度区，均可单独滚动；点击路径按钮按「方向 + 路径索引」移动。
- 无 SSE；sandbox iframe 无 localStorage / 原生 alert → 自绘 modal + textContent 转义 + 模块级 playerId（刷新重新注册）。

## 一致性

- 全部变更在实例锁内（`asyncio.Lock` 不可重入：public 动作不互相调用）。
- aiosqlite 真异步（连接内语句排队），锁内直接 `await`，无需 to_thread。
- 人类移动只改内存；agent 移动额外写回 SQLite（跨对话连续）。

## 数据模型 v3（2026-08-13 已实施）

> v2 模型（id 关联的有向图）经评审后重构，**已实施上线**。核心变化：
> **地块身份从 id 改为 (map_id, 行, 列)**；**连接从独立实体改为地块内嵌的固定 4 方向槽位**；
> **文本引入分时段加权（TextSchedule）**；**引入地块模板（复制预设）**。
> 范围：**单地图 + 引入 map_id**（多地图为将来留位，本次不实现多世界切换）。
> **无迁移**：solo 迭代、未公开，旧 world.db 直接丢弃、播种世界重建（`REFACTOR_PLAN.md` 全部阶段已完成）。

### 决策记录（2026-08-13）

| 决策点 | 结论 |
|---|---|
| 多地图范围 | 单地图 + 引入 map_id（结构就绪，不做多世界切换） |
| 连接目标跨图 | 目标带 `map_id`（空 = 当前地图）；单图阶段恒为空 |
| 隐藏目标 | 保留，挂在**路径级** `reveal_target`（每路径一个布尔）；只展示该路径主目标名（隐藏则 `???`），意外目标名永不展示 |
| 目标语义 | 路径级：每条路径 `targets` 有序（**首个 = 主目标**，展示名据此显示；其余 = 意外路径加权随机，如「脚滑跌下悬崖」「没看路掉进井盖」）；同方向允许多条**平行可选路径**（恢复旧模型「同方向多出口 / +N」能力，总出度不限） |
| 时间语义 | 每日循环钟点窗口（可跨午夜）+ 地图级时区（默认服务器本地） |
| 地块移动 | 坐标只读，专门「移动地块」工具：原子重写指向旧坐标的所有连接目标与该地块上玩家位置；目标格被占则拒绝 |
| 模板 | 复制预设（非继承），应用到空地块 |
| 移动接口 | 按方向 + 路径选择 + 路径内加权抽目标（不再按 exit_id；路径以索引标识，场景内有效） |

### 数据模型（world/v3model.py，已实施）

```python
@dataclass
class TextItem:
    text: str
    weight: float = 1.0


@dataclass
class TextPeriod:
    start: str  # "HH:MM"，每日循环钟点窗口起点
    end: str    # "HH:MM"，终点（可跨午夜）
    items: list[TextItem]


@dataclass
class TextSchedule:
    """分时段加权文本：取当前时间命中的时段，再按权重抽一条文本。

    归一化：缺省 = 单时段全天（00:00–24:00）+ 单条文本权重 1；
    重叠时段按列表顺序先命中者优先。时钟与 PRNG 注入（保证测试确定性）。
    """

    periods: list[TextPeriod] = field(default_factory=lambda: [TextPeriod("00:00", "24:00", [TextItem("", 1.0)])])

    def resolve(self, now, rng) -> str: ...
```

```python
@dataclass
class Target:
    map_id: str = ""  # 空 = 当前地图（单图阶段恒为空）
    row: int
    col: int
    weight: float = 1.0


@dataclass
class ConnectionPath:
    """一条路径（可选出口）：label 为语义文本，targets 有序（首个=主目标，其余=意外）。"""

    label: TextSchedule | None = None  # 路径文本（时段加权，复用 TextSchedule）
    reveal_target: bool = True
    targets: list[Target] = field(default_factory=list)
    # 默认 = 单路径，targets = [方向偏移 1 的相邻地块]（up=行-1 / down=行+1 / left=列-1 / right=列+1）


@dataclass
class ConnectionSlot:
    direction: str    # up/right/down/left，固定不可修改
    enabled: bool = False
    paths: list[ConnectionPath] = field(default_factory=list)
    # 平行路径 = paths 多条（玩家可选）；总出度不限（4 方向 × 多路径 × 多目标）


@dataclass
class Location:
    map_id: str
    row: int
    col: int
    name: str
    description: TextSchedule | None = None
    connections: dict[str, ConnectionSlot]  # 固定键：up/right/down/left（用 dict 而非数组，避免顺序歧义）


@dataclass
class WorldMap:
    id: str
    name: str
    description: TextSchedule | None = None
    timezone: str | None = None  # 地图级时区，None = 服务器本地
    spawn_row: int = 0
    spawn_col: int = 0
```

### 语义细节

- **路径与目标有序性**：槽位 `paths` 列表 = 平行可选路径（`world_look` / 玩家视图逐一列出，玩家选择走哪条）；每条路径 `targets` 有序——**首个目标 = 主目标**（展示名据此显示，隐藏则 `???`），其余目标 = 意外路径（加权随机，仅在沿该路径移动时可能命中）。重排目标 = 改主路径；重排路径 = 改可选项顺序（UI 需排序控件 + 权重可视化）。
- **死引用规则**：**路径级**判定——某路径的主目标不可解析（目标地图不存在 / 目标地块不存在 / 坐标越界）→ 该路径视为死（不展示 / 不可选）；路径内意外目标不可解析 → 静默跳过（意外不存在就当没发生）；槽位启用但全部路径死 → 视为禁用。UI 需区分「显式禁用」与「死引用」（启用但无有效路径 → 标红/虚线提示）。
- **隐藏目标**：`reveal_target` 挂在**路径级**，为假时该路径主目标名显示 `???`；路径内意外目标名永不展示。
- **移动结算**：`move(player, direction, path=None, target=None)` → 解析当前地块该方向槽位；多条路径时 `path` 指定索引（场景内有效，`world_look` 每次重列），单条路径可省略；在选中路径内从 `targets` 按权重抽取目标（显式传入 `target` 坐标则直取）；玩家位置 = 目标 `(map_id, row, col)`（跨图移动会切图）；agent 位置写回。
- **地块移动**：坐标只读（表单不可改）；「移动地块」原子操作 = 改该地块 `(row,col)` + 重写全图指向旧坐标的所有连接目标 + 重写该地块上玩家位置；目标格被占 → 拒绝（不做交换）。
- **模板**：复制预设（CRUD + 应用到空地块），复制 name / description / connections 四槽（含平行路径列表）。目标复制策略：**同图目标存方向相对偏移**（放置时按地块位置平移）；**跨图目标存绝对 `map_id+坐标`** 原样复制。
- **出度不再受限**：方向槽固定 4 个（方向互异升格为结构约束），但同方向允许多条平行路径、路径内多目标——总出度不限，恢复旧模型「同方向多出口 / +N」的可选路径能力（以「方向 + 路径索引」选择，对应旧 exit_id）。

### 持久化（world/store.py）

| 表 | 说明 |
|---|---|
| `maps(id TEXT PK, name, description_json, timezone, spawn_row, spawn_col)` | 地图（本次仅 1 行） |
| `locations(map_id TEXT, row INT, col INT, name, description_json, conns_json, PRIMARY KEY(map_id,row,col))` | 地块；`conns_json` 存 4 槽位配置（纯文本 JSON） |
| `templates(id TEXT PK, name, data_json)` | 地块模板（复制预设） |
| `world_meta(key TEXT PK, value)` | `schema_version` + agent 位置 `(map_id,row,col)` |

**无 v2→v3 迁移**：solo 迭代、未公开，旧库（v2 `locations` / `exits` 表）数据直接丢弃；空库幂等播种新世界（网格小镇：广场 · 步行街 · AstrBot大道 · 开源小区 · AstrBot大学 · 迷雾森林，连接由占位网格自动生成）。

### 接口与工具变化

| 变化 | 说明 |
|---|---|
| `GET /world/state` | 返回地图信息 + 全量地块（含连接槽位）；玩家位置为 `(map_id,row,col)` |
| `POST /world/move` `{player_id, direction, path?, target?}` | 按方向移动（不再用 exit_id）；`path` 为路径索引（多条平行路径时指定），`target` 可选显式指定坐标 |
| `POST /world/location/create` `{row, col, name, description?, template_id?}` | 新建（可用模板）；重复坐标报错 |
| `POST /world/location/update` `{row, col, name?, description?}` | 坐标不可改 |
| `POST /world/location/move` `{row, col, to_row, to_col}` | 移动工具（原子重写引用） |
| `POST /world/location/delete` `{row, col}` | 级联清空指向它的目标；拒绝删除有玩家占据的地块 |
| `POST /world/connection/update` `{row, col, direction, enabled?, paths?}` | 编辑连接槽位（`paths` = 平行路径列表，每条含 label / reveal_target / targets） |
| `POST /world/template/{create,update,delete,apply}` | 模板 CRUD + 应用到空地块 |
| `world_look` | 4 方向槽位，每条平行路径单独列出（label 取时段文本 + 主目标名或 `???`） |
| `world_move(direction, path?)` | 按方向移动（多条平行路径时指定索引） |

## 后续计划

### 数据模型 v3（已完成）

地块身份化（(map_id, 行, 列)）+ 连接内嵌 4 方向槽位 + 分时段加权文本（TextSchedule）+ 地块模板已上线（见上文「数据模型 v3」）。下一步候选：多世界切换（多 map 行 + 跨图移动入口）、实体与交互系统。

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

网格世界编辑：增删地块、编辑 4 方向连接槽位（平行路径增删 / 排序 / 权重）、设置 `reveal_target` / 分时段描述 / 模板。v3 已在插件调试页落地（间隙连线 + 详情栏、查看/编辑子模式、移动地块工具、死引用标红/虚线、模板应用）；v2 独立网页将作为正式的可视化编辑入口。

### 人与 agent 实时互见（SSE 事件流）

全量快照广播（发布时刻拷贝，防共享引用漂移）；`stream_response` + `subscribeSSE`。

### MCP 封装（预留）

AstrBot 无 MCP 服务端；LLM 工具与 MCP 工具收敛为同一 FunctionTool，对 LLM 完全等价。预留 MCP = action 层协议无关；后续把 WorldEngine 抽成独立进程 + FastMCP 薄封装，工具调用路径零改动。

### v3：独立应用

独立仓库的方向预留（移动客户端 / 更完整形态），最后考虑。
