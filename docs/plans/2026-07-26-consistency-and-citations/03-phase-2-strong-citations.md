# 03 Phase 2 任务：强引用知识库

> 前置：Phase 0/1 完成。所有任务 TDD。chunk 结构已确认：`Chunk{text, heading_path, page_no}`（chunking.py:36-40）。

### Task 2.1 chunk 偏移输出
**Files:** Modify `backend/app/services/chunking.py`（Chunk +char_start/char_end；chunk_markdown/chunk_plain_text 回填偏移）
**步骤：**
1. 失败测试：任一 markdown 文本切 chunk 后，`text[s.char_start:s.char_end]`（按原文）== chunk.text；page 标记消耗后偏移仍指向原文区间
2. 实现：splitter 全程跟踪游标；表格/公式原子块偏移取整块边界
**验收：** `pytest tests/test_parsing_chunking.py tests/test_chunk_max_depth.py -q` 全绿 + 新偏移测试 ≥4 条

### Task 2.2 document_chunks 锚点列 + 入库写偏移
**Files:** Modify `backend/app/db/models.py:DocumentChunk`、`backend/app/db/chunk_store.py:24-50`、`backend/app/services/kb_index.py:63-104`；Create migration `0010_chunk_offsets.py`
**步骤：**
1. 测试：`index_source()` 后每行 chunk 有 char_start/end 且 quote_hash 匹配文本
2. 实现落库；`reindex_all()` 回填旧数据
**验收：** `pytest tests/test_kb_index.py -q` 全绿；迁移 upgrade/downgrade 通过

### Task 2.3 CitationAnchor 契约与 evidence 扩展
**Files:** Modify `backend/app/domain/schemas.py`（+CitationAnchor；Evidence +anchors:list[CitationAnchor]=[]）；Create `backend/app/services/citations.py`
**契约：**
```python
class CitationAnchor(BaseModel):
    source_id: str
    chunk_id: str
    page_no: int | None = None
    heading_path: str = ""
    char_start: int | None = None
    char_end: int | None = None
    quote: str = ""
    quote_hash: str = ""
```
**测试：** `_chunk_to_evidence()` 产出的 evidence 携带 anchor；`quote` 为 chunk 文本子串（normalize 空白）
**验收：** `pytest tests/test_kb_index.py tests/test_kb_grounding.py -q` 全绿

### Task 2.4 全文入库默认化（带降级）
**Files:** Modify `backend/app/config.py:fulltext_enrich 默认`、`backend/app/services/ingestion.py`、`backend/app/services/kb_ingest_queue.py`（若名不同以实际为准）
**步骤：**
1. 测试：env 生产 profile 下默认 True；测试 conftest 强制 False 不变
2. ingest 失败单篇不阻塞队列（已有行为，补测试）；origin_url/content_hash 双去重命中 `ingest_jobs`
**验收：** 新测试 ≥5 条全绿；`tests/test_kb_ingest_queue.py` 回归绿

### Task 2.5 hybrid 检索
**Files:** Modify `backend/app/services/kb_index.py:search_chunks`
**步骤：**
1. 失败测试：构造 3 chunk（关键词命中但语义远 / 语义近但无关键词 / 双命中），断言双命中排第一
2. 实现 `score = α·keyword + β·cosine + entity_boost`（α/β 经 Settings，默认 0.4/0.6；无 embedding 时退化为现行为）
**验收：** 新 hybrid 测试 ≥4 条；现有 keyword/embedding 模式测试全绿

### Task 2.6 答案引用绑定 [^n]
**Files:** Modify `backend/app/services/chat_structured.py`、`backend/app/api/chat.py`、Create `backend/app/services/citations.py:bind_answer_citations`
**步骤：**
1. 失败测试：mock LLM 返回含 `[^1]` 答案 → API 响应 `citations[0].anchor` 存在且 quote 属对应 chunk
2. prompt 模板要求逐 claim 标注 `[^n]`；无标注的数值/配方 claim 由 claim_checker 标记 `unsupported`（不删除，仅降级展示）
3. 漂移校验：quote_hash 不匹配 → anchor 标 `stale`，前端灰显
**验收：** `pytest tests/test_chat_structured.py tests/test_claim_checker.py tests/test_kb_index.py::test_chat_merges_kb_chunks -q` 全绿 + 新绑定测试 ≥5 条

### Task 2.7 golden eval 集 + CI 门槛
**Files:** Create `backend/eval/golden_queries.jsonl`（≥20 条：`{query, expected_source_ids[], expected_entities[], must_cite:bool}`）、`backend/scripts/eval_retrieval.py`、CI step
**指标与门槛：**
- `Recall@10 ≥ 0.8`（expected_source_ids 命中）
- `citation precision ≥ 0.9`（答案引用中 quote 校验通过比例）
- `unsupported_claim_rate ≤ 0.2`
**验收：** `python scripts/eval_retrieval.py --golden eval/golden_queries.jsonl` 输出指标并 exit 0；指标低于门槛 exit 1（CI 红）

### Task 2.8 前端引用展示
**Files:** Modify `frontend/src/api.ts`（CitationAnchor 类型）、`frontend/src/components/MarkdownMessage.tsx`、`SourcesPanel.tsx`
**步骤：** `[^n]` 渲染为可点击引用 chip；点击展开 quote + page/heading + “查看原文区间”；stale 灰显
**验收：** `npm run build` 通过；手动 e2e：问答后每条引用可展开
