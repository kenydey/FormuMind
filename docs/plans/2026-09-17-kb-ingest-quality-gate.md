# 入库同源质量门禁（独立方案 + MVP）

> 状态：**已实现**（2026-09-17）  
> 前置：#111 检索侧 `kb_retrieval_gate` 已合入 main（hybrid / probe / recommend）  
> 缺口：脏源仍可经 `index_source` → `document_chunks` 入库；检索门禁只挡 top-k  
> 约束：规则层 only；复用 `kb_retrieval_gate` + `content_filter_enabled`；不默认 LLM judge；不全库回扫；不上 Milvus

## 0. 结论摘要

| 已有 | 证据 |
|------|------|
| 检索门禁 | `kb_retrieval_gate` → `hybrid_search_scored`（#111） |
| Literature 拉取过滤 | `filter_evidence` / `select_ingest_targets` topic |
| 入库 choke | 全路径最终进 `kb_index.index_source` → `ChunkStore.replace_for_source` |

| 本 MVP 做 | 不做 |
|-----------|------|
| `index_source` 写库前同源规则：blocked `origin_url` → 0 chunk；garbage chunk 剔除 | 全库 scrub / 回扫清洗任务 |
| Web `ingest_url` 写入 `SourceDocument.origin_url`（否则域名门永远打不中） | LLM judge / SimHash / wrong_substrate |
| 可选：blocked URL 在 fetch 前 skip（`ingest_url` / `_fetch_one`） | 改 literature 合流语义；改 wiki 入库 |
| 跟随 `content_filter_enabled`（关则 no-op） | 删除已有 SourceDocument 行 |

**注意：** 入库门禁 **不** 应用 `wiki_track`——wiki 仍应能写入 chunk；wiki 仅在检索侧排除。

## 1. 决策锁定

1. **单点 choke**：`index_source` 在 `replace_for_source` 之前 gate；凡经 KB 入库的路径同受保护  
2. **同源规则**：复用 `is_blocked_origin_url` / `is_garbage_chunk_text`；读 `SourceDocument.origin_url`  
3. **blocked → 不写 chunk**：`replace_for_source(source_id, [])` 清空后返回 0（避免脏残留）  
4. **garbage → 剔 chunk**：保留合格行再写；全灭则写空返回 0  
5. **补 origin_url**：`ingestion._ingest_parsed_text(..., origin_url=)`；`ingest_url` 传入 URL  
6. **早停（可选但做）**：`ingest_url` / `_fetch_one` 对 blocked URL 直接 skip，少浪费下载  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-17-kb-ingest-quality-gate.md` | 本方案 |
| `backend/app/services/kb_retrieval_gate.py` | 新增 `gate_ingest_rows` / `ingest_drop_reason` |
| `backend/app/services/kb_index.py` | `index_source` 调用 ingest gate |
| `backend/app/services/ingestion.py` | `origin_url` 透传；blocked URL 早停 |
| `backend/app/services/kb_ingest.py` | `_fetch_one` blocked URL 早停 |
| `backend/tests/test_kb_ingest_quality_gate.py` | 脏源 0 chunk；关 filter 可入库 |

## 3. 验证

- alibaba `origin_url` 的 source：`index_source` 返回 0，`document_chunks` 无行  
- 全 garbage 文本：0 chunk  
- 正常配方文本：仍有 chunk  
- `FORMUMIND_CONTENT_FILTER_ENABLED=false`：blocked 仍可写 chunk  
- 回归：`test_kb_retrieval_gate` / 既有 ingest 绿  
