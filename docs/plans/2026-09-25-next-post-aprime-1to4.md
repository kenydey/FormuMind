# Post-A′ #1–#4：stale→rank · 约束追踪 · scan 扩容 · 项目 soft-correct

> 状态：**已实现**（#1–#4；跳过 #5）  
> 基线：`main @ 264eeef`  
> 分支：`cursor/next-post-aprime-1to4-stale-trace-scan-softcorrect`  
> 用户决策：**做 #1–#4，跳过 #5**（Owner Phase 2 / KG 关系仪表）  
> 约束：不扩 Neo4j；不默认 auto TTL / 关 dual-write / 全局 auto_loop / auto_adopt / soft-correct；Claims/DOE 边界不破

## 落地清单

| # | 交付 |
|---|------|
| **1** | explain `supply_flags` 读材料供应商（缺价 / stale / 长交期）；substitution 候选徽章 + 轻降权 |
| **2** | `Requirement` 生效追踪（objectives/levers/constraints → recommend/DOE/validate）；UI「约束追踪」；单测改 objective → hit 变化 |
| **3** | hybrid 检索 p50/p95 埋点进 quality-ops；`scan_near_cap` 或 p95 超阈时 BM25 预筛 + 子集 cosine（不引 Qdrant）；文档注明 ≠ rag.FAISS |
| **4** | 项目级 `prediction_bias_soft_correct` OR 全局；Workbench 勾选+确认；已有「已校准」徽章统一 |

## 验收

```bash
cd backend && .venv/bin/python -m pytest -q \
  tests/test_formulation_explain.py \
  tests/test_supply_flags_stale.py \
  tests/test_requirement_effect_trace.py \
  tests/test_hybrid_search_ann_gate.py \
  tests/test_kb_quality_ops.py \
  tests/test_prediction_bias_soft_correct.py \
  tests/test_project_soft_correct.py \
  tests/test_top5_rollout_defaults.py

cd frontend && npx vitest run \
  src/components/FormulaLeaderboard.explain.test.tsx \
  src/components/ConstraintEffectTrace.test.tsx \
  src/projectWorkspace.test.ts
```
