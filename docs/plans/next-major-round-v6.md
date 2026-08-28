# 下一大轮（v6）规划 — 稳定性与知识深化

> 基于 `678bf44` 扫描，承接 v5 已交付（自主闭环+替代+可观测）。共 3 项。

## 0. 基线（已冻结 `678bf44`）

| 域 | 已交付 |
|---|---|
| 闭环 | loop 自主+限轮，bias-trend 联动 |
| 知识 | KG inhibits/measured 闭环 + 替代一键 |
| 可观测 | elapsed_ms 透传 |

## 1. P1 — KG 默认开启与权重校准 ✅
- `config.kg_enabled` 默认 `False→True`（环境变量可覆盖）
- `GET /api/kg/calibration`：penalty/bonus + 命中计数（inhibits/substitutes/synergizes）
- 提交 `fd24145`

## 2. P1 — 实验→训练一致性加固 ✅
- 根因：两处裸 `session.commit()` 绕过 Redis 写锁（uvicorn+celery 并发撞 `database is locked`）
  - `kg_feedback.ingest_measured_evidence` → `commit_session`
  - `workbench_training.ensure_experiment_for_row` → `commit_session`
- `sync` 后前端自动 `recomputePredicted`（经 `validateFormulations` 重算 cost/voc 等）

## 3. P2 — 成本/碳足迹目标透传 ✅
- `LoopReport.cost_summary`：top 配方 cost_cny_per_kg/voc_gpl 均值
- `FormulaLeaderboard` 展示 cost/voc 徽标
- `LoopModal` 状态行展示均成本/均VOC

改动估算：6-8 文件，风险低

