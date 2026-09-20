# KG 自进化 · 材料级实测回流（独立方案 + MVP）

> 状态：**已实现**（2026-09-16）  
> 基线：v1（`kg_feedback.ingest_measured_evidence`）已在台账 sync 挂接，写 **domain → property**  
> 缺口：回流粒度停在「领域」，无法反哺组分级推荐 / 协同排序；本方案把飞轮升级到 **material → property**  
> 约束：不双写 Neo4j；不挂 `loop_history`；不改硬 INHIBITS；sync 失败永不阻断

## 0. 结论摘要

| 已有 | 证据 |
|------|------|
| Sync 挂钩 + `kg_written` | `experiments.py` → `kg_feedback.ingest_measured_evidence`；`LabWorkbench` 提示 |
| provenance 分隔 | `extraction_method="measured"`；`source_id=measured:campaign_{id}` |
| 可观测 | `GET /api/kg/feedback/stats`；`KgRelationPanel`「实测」徽章 |

| 本 MVP 做 | 不做 |
|-----------|------|
| 从 `actual_params`/`planned_params` 解析组分 → 写 `measured_performance`（材料→指标） | Neo4j 双写、新 link_type、改推荐权重公式 |
| 域级边保留为汇总兜底 | 挂在 `loop_history` 之后 |
| evidence 增补 `project_id`；stats 区分 material / domain | 新 Hub 项目图谱、通知种类 |

## 1. 决策锁定

1. **触发点不变**：台账 `PUT .../workbench/sync`（实测已落库处）  
2. **写入优先级**：有因子参数 → **材料→指标**；同时保留 **域→指标**（兼容旧读路径 / `_has_measured_evidence`）  
3. **实体缺失**：指标继续 `_resolve_or_create_property`；材料 `_resolve_or_create_material`（`kind=chemical`，id=`mat:{slug}`）  
4. **provenance**：`extraction_method="measured"`；`source_id` 仍 `measured:campaign_{id}`（stats 聚合不变）；`sentence` 含材料名与数值；可选 `project_id` 字段  
5. **门控**：`kg_measured_feedback_enabled`（默认开）

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-16-kg-self-evolution-material.md` | 本方案 |
| `backend/app/services/kg_feedback.py` | 材料级边 + project_id |
| `backend/app/api/kg.py` | feedback stats 增加 `measured_material` / `measured_domain` |
| `backend/tests/test_kg_feedback.py` | 材料边用例；域缺失时材料仍可写 |
| `frontend/src/components/KgRelationPanel.tsx` | 展示材料实测条数（轻） |

## 3. 验证

- 单测：带 `actual_params` 的 Completed 行 → 出现 `mat:* → prop:*` measured 边，且文献 evidence 不丢  
- 域实体缺失时：材料边仍写入（`written > 0`）  
- `GET /api/kg/feedback/stats` 含 `measured_material ≥ 1`  
- 回归：`test_kg_provenance.py` / 原 domain 用例仍绿  
