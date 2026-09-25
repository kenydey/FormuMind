# Next Top-5‴：Wiki embed · bias soft-correct · Hub MRR · 项目级 auto_patch · 页图+空态

> 状态：**已实现**（2026-09-25）  
> 前置：`main @ 47946fd`（Top-5″ soft-degrade）  
> 分支：`cursor/next-top5-embed-bias-mrr-autopatch-pagegraph`  
> 顺序：**#1 → #5**

## 0. 做 / 不做

| # | 做 | 不做 |
|---|----|------|
| 1 | `wiki_embed_enabled` **默认 True**；Hub CTA/文案；Claims 仍滤 wiki | 第二向量库；LLM 洗 L1 |
| 2 | `prediction_bias_soft_correct` **默认 False**；n≥min 时 predicted − mean_error + 芯片 | 改 measured；默认开；破 Claims/DOE |
| 3 | Hub Golden 展示 MRR/Recall@k + localStorage 趋势 | 拖慢主 CI；新建 golden yaml |
| 4 | `ProjectWorkspace.wiki_dossier_auto_patch`；`global OR project`；Workbench/Hub 项目开关 | 全局默认 True；llm_narrative |
| 5 | `wiki_page_graph_enabled` **默认 True**；配方榜空态 CTA | Neo4j；混材料 KG |

## 1. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_wiki_p3_embed.py \
  tests/test_prediction_bias_soft_correct.py \
  tests/test_top5_rollout_defaults.py \
  tests/test_wiki_p4_dossier.py \
  tests/test_wiki_page_graph.py \
  tests/test_golden_rd_loop_gate.py
cd frontend && npx vitest run \
  src/components/knowledge-hub/RetrievalProbePanel.test.tsx \
  src/components/knowledge-hub/HubWikiGraphPane.test.tsx \
  src/components/FormulaLeaderboard*.test.tsx
```
