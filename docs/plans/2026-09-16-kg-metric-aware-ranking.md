# KG 实测边 → 指标感知推荐排序（独立方案 + MVP）

> 状态：**已实现**（2026-09-16）  
> 前置：[#106](https://github.com/kenydey/FormuMind/pull/106) 材料级 `mat:* → prop:*` measured 回流已合入 `main`  
> 缺口：读侧 `kg_compat_adjust` 仅做「有任意 measured 边 → ×1.15」；且 `_score_and_validate` 在算分**之前**调用它，加成常被后续 `form.score = predicted` 覆盖  
> 约束：软排序不删候选；不改硬 INHIBITS / DOE `infeasible`；不双写 Neo4j；不挂 `loop_history`

## 0. 结论摘要

| 已有 | 证据 |
|------|------|
| 材料级写入 | `kg_feedback.ingest_measured_evidence` → `measured_performance` + `granularity=material` |
| 读侧挂钩 | `workflow._score_and_validate(chem_screen=True)` → `kg_compat_adjust` |
| 二元加成 | `_has_measured_evidence` + `kg_measured_bonus`（默认 1.15） |

| 本 MVP 做 | 不做 |
|-----------|------|
| **先算分再调权**：`kg_compat_adjust` 挪到 objectives/predicted 赋分之后 | 改硬 INHIBITS / DOE 门禁 |
| 按 `req.objectives` 目标指标匹配 `mat → prop:{metric}` 实测边 | Neo4j 双写、新 link_type |
| 目标指标命中：好实测加成 / 差实测轻降权；无关指标不发「任意 measured」满额加成 | 大改前端（复用 `kg_compat` + warnings） |
| evidence 写入补 `measured_value` + `metric`（便于读侧，不靠正则猜） | 改推荐权重主公式 / multi_objective 本身 |

## 1. 决策锁定

1. **调用顺序**：`_score_and_validate` 先写 `form.score`（单目标 predicted / 多目标 multi_objective），再 `kg_compat_adjust(form, objectives=…)`  
2. **指标解析**：对每个 `ObjectiveSpec.metric` 解析 `prop:{metric}`（或 search 命中）；查材料实体的 `measured_performance` 出边  
3. **质量信号**（软、可开关）：  
   - 优先 `evidence_ref.measured_value`；否则回退 link `confidence`  
   - 与 `form.predicted[metric]` 比（若有）：maximize 下 `value >= 0.9×pred` → good；`value <= 0.5×pred` → poor；否则 presence  
   - 无 predicted 时：`confidence >= 0.7` good；`confidence < 0.55` poor；否则 presence  
4. **系数**（config）：`kg_measured_metric_bonus`（默认 1.12）、`kg_measured_metric_penalty`（默认 0.92）、`kg_measured_metric_presence`（默认 1.05）；无目标指标命中时保留旧 `kg_measured_bonus` 兜底  
5. **INHIBITS 优先**：`feasible=False` 只罚不奖（与现逻辑一致）  
6. **透明**：`form.kg_compat.measured_metric_hits` 列出材料/指标/质量档；warning 文案区分「目标指标实测加成/降权」

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-16-kg-metric-aware-ranking.md` | 本方案 |
| `backend/app/config.py` | metric bonus/penalty/presence 三项 |
| `backend/app/services/kg_feedback.py` | evidence_ref 增 `measured_value` / `metric` |
| `backend/app/services/kg_chemical_check.py` | 收集目标指标 measured hits（可选辅助） |
| `backend/app/services/kg_recommend_score.py` | 指标感知调权 + `record_kg_compat` 扩展 |
| `backend/app/pipeline/workflow.py` | 调权挪到赋分之后并传入 objectives |
| `backend/tests/test_kg_measured_bonus.py` | 扩展 / 新用例：目标指标命中、差实测降权、顺序不丢加成 |
| `backend/tests/test_recommend_kg_ranking.py` | 断言 chem_screen 路径加成真正留在 score 上 |

## 3. 验证

- 单测：盐雾目标下，有 `mat→prop:salt_spray_hours` 好实测的配方 score > 无实测 / 差实测  
- 单测：`_score_and_validate(chem_screen=True)` 后 `score` 仍含 KG 系数（不再被 predicted 覆盖）  
- 回归：INHIBITS 仍只罚；`test_kg_recommend_score` / `test_kg_measured_bonus` / `test_recommend_kg_ranking` 全绿  
