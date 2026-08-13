<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD025 -->
<!-- markdownlint-disable MD041 -->
# 重构计划：数据模型 v3

> 配套设计：DESIGN.md「数据模型重构（v3 目标模型，规划中）」。
> 开发阶段遵循快速迭代惯例：直接 commit + push，验证交给 GitHub CI（pytest + js-check + ruff-format）与手动测试。

## 目标

把世界的数据底座从「id 关联的有向图（Location/Exit 独立实体）」重构为「以 (map_id, 行, 列) 为身份的地块 + 内嵌固定 4 方向连接槽位 + 分时段加权文本（TextSchedule）+ 地块模板」。单地图 + map_id 结构就绪，本次不实现多世界切换。

破坏性变化（v2 → v3）：移动接口从按 exit_id 改为按方向；地块坐标从可改改为只读（移动走专门工具）；同方向多出口的「平行可选路径」收敛为「主路径 + 意外路径」加权模型。

## 阶段

### Phase 1 — world/model.py：目标数据类 + TextSchedule

- [ ] 新增 `TextSchedule` / `TextPeriod` / `TextItem`：`resolve(now, rng) -> str`；`from_dict` / `to_dict` 归一化（缺省 = 单时段全天单条权重 1；重叠时段先命中者优先）
- [ ] 新增 `Target(map_id, row, col, weight)` / `ConnectionSlot`（固定 direction；默认目标 = 方向偏移 1）/ `Location` / `WorldMap`
- [ ] 方向 ↔ 坐标偏移常量（up=行-1 / down=行+1 / left=列-1 / right=列+1，与前端 `DIR_OFFSETS` 一致）
- [ ] 时钟与 PRNG 注入点（`describe_scene` / 移动结算用，保证测试确定性）
- [ ] 单测：时段命中（含跨午夜 22:00–02:00）、加权抽取、归一化、序列化往返

### Phase 2 — world/store.py：新 schema + 迁移

- [ ] 新表 `maps` / `locations`(map_id,row,col 组合 PK) / `templates` / `world_meta`
- [ ] `_migrate()` v2 → v3：建默认地图；layout 坐标 → (row,col)（无坐标 → firstFreeCell 兜底）；exits → 方向槽位（direction → 槽、to_id → 目标坐标、reveal_target 保留）；agent 位置改写
- [ ] 内存索引：`maps` / `loc_by_pos[(map_id,row,col)]`
- [ ] 目标可解析性校验辅助（含跨图：map_id 空 = 当前图；目标地图/地块不存在 → 不可解析）
- [ ] 迁移单测：老库 → 新库数据保真（地块、连接、reveal_target、agent 位置）

### Phase 3 — world/engine.py：动作重写

- [ ] `create_location(row,col,name,description?)`（重复坐标报错）/ `update_location`（坐标只读）/ `delete_location`（级联清空指向它的目标 + 拒绝删除有玩家占据的地块）
- [ ] `move_location(row,col,to_row,to_col)`：原子重写自身坐标 + 全图指向旧坐标的连接目标 + 该地块上玩家位置；目标格被占 → 拒绝
- [ ] `update_connection(row,col,direction,enabled?,label?,reveal_target?,targets?)`（方向不可改）
- [ ] `move(player, direction, target=None)`：死引用规则（首个目标不可解析 → 整槽禁用；其余目标不可解析 → 静默跳过）→ 加权抽目标 → 更新玩家位置（跨图切图）→ agent 写回
- [ ] `describe_scene`：时间感知描述 + 4 方向槽位 + 只显示首个目标名（隐藏则 `???`）
- [ ] 模板：`create/update/delete_template` + `apply_template(row,col,template_id)`（同图目标偏移平移、跨图目标原样复制）
- [ ] 引擎单测：全部动作 + 死引用 + 加权抽取 + 移动地块引用联动（全图引用扫描断言）

### Phase 4 — api/：端点重写

- [ ] `GET /world/state`（地图信息 + 全量地块含连接槽位；玩家位置为 (map_id,row,col)）
- [ ] `POST /world/move {player_id, direction, target?}`
- [ ] `POST /world/location/{create,update,delete,move}`
- [ ] `POST /world/connection/update`
- [ ] `POST /world/template/{create,update,delete,apply}`
- [ ] API 单测（handler 类型校验 + WorldError → 400 信封；update 按 payload 键拼 kwargs 的惯例保留）

### Phase 5 — main.py：LLM 工具 + 种子世界

- [ ] `world_look`：新场景格式（4 方向槽位、label 取时段文本、目标只显示首个目标名 / `???`）
- [ ] `world_move(direction)`：docstring 更新（参数带类型注解 `direction(string)`）
- [ ] 播种世界按新模型重建（小镇 + 迷雾区：多目标加权 / 隐藏目标 / 环路）

### Phase 6 — pages/world/：前端

- [ ] `shared.js`：`computePositions` 改读 (row,col)；`DIR_OFFSETS` 与引擎偏移常量对齐
- [ ] `edit-view.js`：渲染新模型（地块 + 4 槽位连接）；点间隙编辑两侧槽位；坐标只读；「移动地块」工具；死引用槽位标红/虚线；模板应用（点空地块 → 从模板创建）
- [ ] `edit-forms.js`：`TextSchedule` 编辑器（默认纯文本框，高级折叠展开时段 / 多条 / 权重百分比）；目标列表编辑器（排序 + 权重）；模板表单
- [ ] `play-view.js`：4 方向槽位；只显示首个目标名（/`???`）
- [ ] `style.css`：死引用样式等新增类
- [ ] js-check 覆盖全部前端模块（CI）

### Phase 7 — 收尾

- [ ] 引擎 / API / 迁移测试全量绿（GitHub Actions：pytest + js-check + ruff-format）
- [ ] CHANGELOG v3 破坏性变更条目（含迁移说明）
- [ ] README / DESIGN 现状部分更新到新模型

## 关键风险与对策

| 风险 | 对策 |
|---|---|
| 移动地块引用重写遗漏（最易错） | 引擎原子操作 + 专项单测（全图引用扫描断言） |
| 迁移保真（老世界数据不丢） | 迁移单测对比前后数据集；播种世界重建 |
| 时间 / 随机导致测试不稳定 | 时钟与 PRNG 注入，单测全注入 |
| 前端 / 引擎方向与坐标映射不一致 | 共享偏移常量；js-check + 手动测试重点覆盖 |
| 平行可选路径语义被移除的体验变化 | 文档明示「主路径 + 意外路径」模型；world_look 措辞更新 |

## 验证策略

- 每个 Phase 完成后直接 commit + push（快速迭代惯例），GitHub Actions 把关（pytest + js-check + ruff-format）。
- 手动测试重点：移动地块后全图引用正确、分时段描述按时切换、多目标加权随机、模板应用后目标平移正确、隐藏目标 `???`、死引用提示。
- 用户显式要求辅助验证时，用 DOM-stub harness 抓前端运行时逻辑（编辑视图重绘 / 连接渲染 / 缩略图定位类 bug）。
