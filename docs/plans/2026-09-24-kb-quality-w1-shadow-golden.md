# KB 质量 W1：shadow 可观测 + Golden MRR/CI

> 状态：**已实现**（2026-09-24）  
> 前置：升级评审结论 — shadow 先度量再闸；golden 延用 `golden_retrieval.py` 加 MRR/Recall，不新建 yaml  
> 约束：不默认 flip `kb_relevance_shadow`；不默认开 `auto_loop`；不扩 Wiki/Neo4j

## 0. 本 PR 做 / 不做

| 做 | 不做 |
|----|------|
| W1：每批 ingest 持久化 topicality shadow 摘要（JSONL + audit 一行） | 改默认真闸 / 改 `kb_ingest_min_relevance` 语义 |
| W1：`GET /api/kb/relevance-shadow/stats` 聚合 p10/p50/p90、将拒收%、与 topic_gate 重叠 | 前端大面板（可后接 Hub） |
| W1′：`run_golden_eval` 增加 MRR / Recall@k | 新建 golden_queries.yaml |
| W1′：CI 独立 `golden` job（`ci_golden_gate.sh`） | 幻想 nDCG |

## 1. 文件

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-24-kb-quality-w1-shadow-golden.md` | 本方案 |
| `backend/app/services/kb_ingest.py` | shadow 批摘要 + topic 重叠计数 |
| `backend/app/services/kb_ingest_audit.py` | `record_relevance_shadow_batch` JSONL |
| `backend/app/api/kb.py` | stats 端点 |
| `backend/app/services/kb_query_test.py` | MRR / Recall@k |
| `backend/tests/test_*.py` | 单测 |
| `.github/workflows/ci.yml` | golden job |

## 2. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_openalex_arms.py -k 'shadow or topicality' \
  tests/test_relevance_shadow_stats.py \
  tests/test_golden_eval_metrics.py
bash scripts/ci_golden_gate.sh   # 或 CI golden job
```
