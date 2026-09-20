# 检索探针参数回灌推荐主路径（独立方案 + MVP）

> 状态：**已实现**（2026-09-17）  
> 前置：Hub「检索探针」(#101) 可调 `alpha` / 项目作用域 / `hybrid_rerank`；KG 飞轮 #106–#108 已结束  
> 缺口：探针验证的是 `hybrid_search_scored`（document_chunks），推荐主路径 `resolve_grounded_evidence` 只走 ColBERT/BM25 **Evidence 注册表**，research 的 KB 融合仍是 `search_chunks`（无 α、默认无 project）  
> 约束：不引入 Milvus / 第二向量库；不改 chat 检索；不把 `search_rerank_enabled` 全局翻给推荐；ColBERT 主召回保留，hybrid 为融合层

## 0. 结论摘要

| 已有 | 证据 |
|------|------|
| 探针 | `POST /api/kb/query-test` → `run_query_test` → `hybrid_search_scored` |
| 推荐轻量检索 | `research_graph.resolve_grounded_evidence` → `colbert_store.search` |
| Research KB 融合 | `retrieve_node` → `kb_index.search_chunks`（`kb_recommend_top_k`） |

| 本 MVP 做 | 不做 |
|-----------|------|
| 共享配置：`kb_hybrid_alpha`、`kb_recommend_use_hybrid`、`kb_recommend_include_global`、`kb_recommend_rerank_enabled`（默认关） | Yuxi Evaluation Workspace / Milvus |
| `hybrid_search*` / `run_query_test` 默认 α 读 settings | 改 chat / literature 的 rerank 语义 |
| `resolve_grounded_evidence` 在 ColBERT 后融合 hybrid Evidence（project + α） | 用 hybrid **替换** ColBERT |
| `retrieve_node` 非 KG 分支：hybrid 替代裸 `search_chunks`（同开关） | 每会话「应用探针」持久化 UI |

## 1. 决策锁定

1. **共享旋钮**：探针与推荐读同一 `kb_hybrid_alpha`（默认 0.3，与现探针 UI 一致）  
2. **推荐融合**：`kb_recommend_use_hybrid=True` 且 `kb_recommend_top_k>0` 且 KB v2 开 → 调 `search_chunks_hybrid`  
3. **项目作用域**：`req.project_id`（空则全局）；`kb_recommend_include_global` 默认 True（对齐探针 `project_global`）  
4. **重排**：仅当 `kb_recommend_rerank_enabled`（默认 False）时对融合后的证据池做 `llm_rerank`；**不**改 `search_rerank_enabled`  
5. **降级**：hybrid 失败 / 空 → 保留 ColBERT 结果，不抛错

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-17-probe-feed-recommend.md` | 本方案 |
| `backend/app/config.py` | 四个共享旋钮 |
| `backend/app/services/hybrid_search.py` | α 默认从 settings |
| `backend/app/services/kb_query_test.py` | α 默认从 settings |
| `backend/app/services/kb_index.py` | `search_chunks_hybrid` |
| `backend/app/pipeline/research_graph.py` | recommend + retrieve 融合 |
| `backend/tests/test_kb_grounding.py` / `test_hybrid_search.py` | 扩展 |

## 3. 验证

- hybrid α 跟随 `FORMUMIND_KB_HYBRID_ALPHA`  
- `resolve_grounded_evidence` 在 mock hybrid 下并入 `kb:*` Evidence，且 `kb_recommend_top_k=0` 不调用  
- `kb_recommend_use_hybrid=false` 时 retrieve 仍走 `search_chunks`  
- 回归：既有 hybrid / grounding / query-test 绿  
