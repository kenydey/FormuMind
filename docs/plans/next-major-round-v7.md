# 下一大轮（v7）规划 — 清理债与一致性补全

> 基于 `v6-freeze`（adc5b18）扫描，承接 v6 已交付（KG 默认开启、训练→展示 sync 一致性、成本碳足迹透传）。共 3 项，均为**已确认的真实缺口**，无凭空设想。

## 0. 基线（已冻结 `v6-freeze` → `adc5b18`）

| 域 | 已交付 |
|---|---|
| KG | 默认开启 + calibration + measured 闭环 + 替代 |
| 闭环 | loop 自主+限轮，sync 后自动 recomputePredicted |
| 一致性 | `kg_feedback`/`workbench_training` 锁安全修复 |
| 可观测 | elapsed_ms + 成本/碳足迹透传 |

## 1. P1 — 清理死限流规则（配置债）

**证据**：`backend/app/middleware/rate_limit.py:35`
```
("POST", "/api/baybe/recommend", 10, 60.0),
```
该路由在 v2 已删除（`docs/plans/next-upgrade-priorities-v2.md` → `eb56548` 死 API 清理），但限流表仍残留此条。后果：
- 死配置，永不命中，纯噪声，且误导后续维护者以为该端点仍存在。
- 证明 v2 的“死 API 清理”不彻底（只删了路由，没扫 middleware/文档/前端）。

**方案**：
- 删除 `rate_limit.py:35` 死规则。
- 顺带核查 `rate_limit.py` 其余规则是否都有对应存活路由（grep 每个 path 是否存在 `api/*.py` 的 router 定义），一并清理。
- 核查 `CODE_WIKI.md` / 前端是否仍有 `baybe/recommend` 引用（若有则删）。

**风险**：低。`_rule_for` 按前缀匹配，删一条不影响其他。

## 2. P1 — KG 链接表复合索引（性能债）

**证据**：`backend/app/db/models.py:418-437` `KGEntityLink`
```python
src_entity_id: Mapped[str] = mapped_column(String(64), index=True)   # 单列
dst_entity_id: Mapped[str] = mapped_column(String(64), index=True)   # 单列
link_type:    Mapped[str] = mapped_column(String(32), index=True)    # 单列
__table_args__ = (UniqueConstraint("src_entity_id","dst_entity_id","link_type",...),)
```
但 `backend/app/db/entity_store.py` 中 **9 处热点查询** 均为 `(src_entity_id, link_type)` 复合过滤：
- `:202-204`（merge 去重）、`:273-275`、`:307-309`、`:360-362`、`:394-396`（语义/结构链接批量拉取）
- `:417`、`:425`（get_links_for_entity）、`:546`（图谱）
- `backend/app/services/kg_feedback.py:177-179`（measured_performance 查询）
- `backend/app/api/kg.py:58`（report 按 extraction_method 过滤 + :116-118 counts）

单列索引下，SQLite/Postgres 对每个 `src_entity_id` 命中后再内存过滤 `link_type`，知识库规模增长（实测证据持续回流 v5 飞轮）后图谱查询与 counts 退化。

**方案**：
- `models.py` 给 `KGEntityLink` 加复合索引：
  ```python
  Index("ix_kb_link_src_type", "src_entity_id", "link_type"),
  Index("ix_kb_link_dst_type", "dst_entity_id", "link_type"),
  ```
- 新增 alembic 迁移 `0018_kg_link_composite_index.py`（`op.create_index` + `op.drop_index` 回滚），对齐现有迁移风格（`0015_inferred_systems.py:57` 同款 `op.create_index`）。
- 迁移后跑 `alembic upgrade head` 验证空库建表 + 现有库加索引均通过（复用 `test_alembic_migrations.py` 的 `RuntimeError` 守卫）。

**风险**：低（加索引幂等；`test_alembic_migrations` 会拦住任何运行时 ALTER）。中风险点是迁移在已存在数据的库上执行耗时——加索引对中小库可忽略。

**价值**：直接支撑 v5 KG 自进化飞轮在规模增长后的查询性能，避免“越用越慢”。

## 3. P1 — 训练→展示一致性补全（功能债）

**证据**：v6 已加 `recomputePredicted`（前端 `workflowSlice.ts`），但仅接在 `LabWorkbench` sync 之后（`LabWorkbench.tsx:350`）。
未接的路径：
- `workflowSlice.importCsv`（`:572-594`）：CSV 导入训练后调 `runResearch()`，但 leaderboard 的 `predicted`（cost/voc 等）未重算。
- `workflowSlice.adoptDoePlanToWorkbench`（DOE 采纳到台账后，模型若更新，leaderboard 预测未重算）。

后果：用户在“导入 CSV 重新训练”或“采纳 DOE 到台账”后，leaderboard 卡片上的 cost/voc 徽标（v6 P2 新增）仍是旧模型预测值，与当前模型不一致——与 v6 修的 sync 路径问题同源。

**方案**：
- 在 `importCsv` 成功分支末尾加 `await get().recomputePredicted();`
- 在 `adoptDoePlanToWorkbench` 成功分支末尾加 `await get().recomputePredicted();`
- 复用 v6 已落地的 `recomputePredicted`（经 `validateFormulations` 重算 cost/voc）。

**风险**：低（纯前端，复用既有 action；`recomputePredicted` 内部已 try/catch 且 leaderboard 为空时直接 return）。

## 4. 执行顺序与验证

| 序 | 项 | 核心文件 | 验证 |
|---|---|---|---|
| 1 | 死限流清理 | `middleware/rate_limit.py` | grep 所有规则 path 均在 `api/*.py` 存在；`tsc` PASS；启动无警告 |
| 2 | KG 复合索引 | `models.py` + `alembic/versions/0018_*` | `alembic upgrade head` 通过；`test_alembic_migrations` 全绿 |
| 3 | 一致性补全 | `workflowSlice.ts` | `tsc` PASS；新增测试：`importCsv`/`adoptDoePlan` 后 leaderboard 预测刷新（后端或前端单测） |

改动估算：4-5 文件，风险低，全部为债清理与同源补全覆盖。

## 5. 风险矩阵

| 项 | 技术风险 | 业务价值 | 优先级 |
|----|---------|---------|--------|
| 死限流清理 | 低 | 中（可维护性/正确性） | P1 |
| KG 复合索引 | 低-中（迁移耗时） | 高（飞轮性能） | P1 |
| 一致性补全 | 低 | 中（数据可信） | P1 |
