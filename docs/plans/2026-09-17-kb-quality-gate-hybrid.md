# 语料质量门禁进推荐/探针路径（独立方案 + MVP）

> 状态：**已实现**（2026-09-17）  
> 前置：检索回灌 #109/#110 已收口；探针与推荐共享 `hybrid_search_scored`  
> 缺口：literature 合流有 `content_filter` / deny；KB hybrid **无**内容质量门——脏 `origin_url`（如 alibaba）一旦入库即可进 top-k  
> 约束：规则层 only；复用 `content_filter_enabled`；不默认开 LLM judge；不改 literature 语义；不上 Milvus

## 0. 结论摘要

| 已有 | 证据 |
|------|------|
| Literature 过滤 | `literature._merge_filter_rank` → `filter_evidence` |
| 入库门禁 | `select_ingest_targets` / `topic_gate`（不回扫已入库） |
| 共享 hybrid | `hybrid_search_scored` ← probe + `search_chunks_hybrid` |

| 本 MVP 做 | 不做 |
|-----------|------|
| `gate_scored_chunks`：blocked domain（`origin_url`）+ garbage 文本 + wiki 排除 | LLM judge / SimHash / wrong_substrate |
| 挂在 `hybrid_search_scored` 排序后、凑满 `top_k` 前 | 改 chat / literature 合流 |
| 跟随 `content_filter_enabled`（关则 no-op） | 全库回扫清洗任务 |

## 1. 决策锁定

1. **单点接线**：只改 `hybrid_search_scored` → 探针与推荐 fuse 同时受益  
2. **凑满 top_k**：先按 hybrid 分排序全序，再 gate 直到取满 k（避免过滤后不足）  
3. **Blocked domain**：对 `SourceDocument.origin_url` 复用 `content_filter` 域名表  
4. **Garbage**：chunk 正文过短 / 符号比过高（同 literature 阈值）  
5. **Wiki**：对齐 `search_chunks` 的 `list_wiki_source_ids` 排除  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-17-kb-quality-gate-hybrid.md` | 本方案 |
| `backend/app/services/kb_retrieval_gate.py` | 新建 gate |
| `backend/app/services/hybrid_search.py` | 调用 gate |
| `backend/tests/test_kb_retrieval_gate.py` | 脏源不进 top-k |

## 3. 验证

- alibaba `origin_url` 的 chunk 在 hybrid top-k 中消失；关 `content_filter_enabled` 可再现  
- 正常本地配方 chunk 仍可召回  
- 回归：既有 hybrid / grounding / query-test 绿  
