# FormuMind RAG 与 LLM Wiki 下一步优化方案（2026-09-26）

> 基于 HEAD `c76b1fd` 的代码复审。你上一轮落地的 commits（cross-encoder rerank、引用页码锚点、STORM claim 接线、混合权重统一、多语 embedding catalog、conformal 不确定度等）已全部逐项核实为**真实落地且有测试覆盖**。
> 本方案只讲**下一步**：先澄清三个"以为做了但实际没做"的点，再给 RAG 和 Wiki 各自的 P0/P1/P2。

## 0. 先澄清：三个"以为做了"的点

| 以为 | 实际 | 下一步动作 |
|---|---|---|
| Qwen3-Embedding 已是默认 | 只是 catalog 可选项（`rag.py:87-112`）；默认仍是 MiniLM + bge-small-zh 双语分流（`kb_index.py:262-301`）；且本机 venv 未装 sentence-transformers，语义检索本地仍不生效 | RAG P0-1：改默认 + 重建索引 |
| cross-encoder rerank 已上线 | 已实现（`rag.py:667`，bge-reranker-base）但**默认关闭**，且只接在探针/文献链路，**chat 主链路未接** | RAG P0-2：chat 接入 |
| DeepEval 已成门禁 | CI job 存在但默认 `FORMUMIND_DEEPEVAL=0` 跳过；golden 53 问断言的仍是关键词命中，无 faithfulness 门禁 | RAG P2-9：先 nightly 非阻塞跑数 |

---

## 1. RAG 下一步优化

### P0（检索质量，改动小、收益确定）

**R1. 默认 embedding 切 Qwen3-Embedding-0.6B 并重建索引**
- 现状：统一 embedding 空间只是 opt-in；默认双语小模型跨语言查询退化为关键词。
- 改动：`config.py:183` 给出推荐默认（或部署文档强推 `FORMUMIND_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B`），本机先 `pip install -e '.[embedding]'` 装上 sentence-transformers，然后跑 `kb_index.reindex_all` 重建向量。
- 注意：`bge_query_prefix` 只对 bge 系加查询指令，Qwen3 不需要，不要误加。

**R2. chat 主链路接入 cross-encoder**
- 现状：`rerank_cross_encoder_scored`（`rag.py:667`）+ 统一入口 `rerank_scored`（`:733`）已实现，失败标记机制（`(items, applied)` + meta reason）已就绪且不再静默，但 chat 主链路（`llm.py:1956`）仍只做截断。
- 改动：`chat_rerank_candidates=50 → CE top_k=20`，flag 默认关；CPU 实测延迟后决定是否默认开。同步加 `cross_encoder_timeout_s` + 候选截 top 30，超时走已有 `applied=False` 降级（现在 50 候选一次 predict，CPU 上秒级，无超时控制）。

**R3. SimHash 去重**
- 现状：`chunking.py` 无去重（全仓 grep 零命中）；同一文档以不同 source_id 重复入库产生重复行，污染排序。
- 改动：`kb_index.py:index_source` 内加 64 位 SimHash，汉明距离 ≤3 判重跳过。这是当前 chunk 层最便宜的质量修复。

### P1（检索质量与延迟）

**R4. RRF 替代加权融合**（`hybrid_search.py:hybrid_search_scored`、`rag.py:BM25FAISSStore.query`）
- 现状：`alpha·BM25 + (1-alpha)·cosine` 各自 max 归一，候选少时不稳定；权重已统一为 `kb_hybrid_alpha`（0.3）。
- 改动：做成 `kb_hybrid_fusion: weighted|rrf` 开关，用 53 问 golden 做 A/B 再定默认。

**R5. 按 project 预筛 + scan 超限告警**
- 现状：`all_chunks(limit=5000)` 全表扫是主要成本，ANN 门只是缓解；超 5000 按时间截断，召回有硬天花板。
- 改动：`hybrid_search_scored` 入口按 `project_id` 先过滤（参数已支持），超限记 warning 让线上可见。

**R6. 查询改写（可选，默认关）**
- 化学实体扩展（`_entity_boost`）已有，但查询侧无改写。对短查询/中文口语查询加 HyDE 或 LLM query expansion，flag 控制。

### P2（规模化与评估）

**R7. 向量库仍是最大缺口**：65d609e 的进程内 matrix cosine 是优秀的过渡方案，但 5000 上限 + 进程内扫描是硬天花板。chunk 持续增长后上 Qdrant（新增 `services/qdrant_store.py` 替换进程内余弦）或 pgvector。

**R8. DeepEval 从"跳过"改为 nightly 非阻塞上报**：CI job 已有（`continue-on-error`），先让 faithfulness / answer-relevancy 在有 judge key 的环境跑起来看数，再收紧为门禁。

**R9. 检索排序指标**：golden 只断言"找得到"（关键词命中）。加 `ContextualPrecisionMetric` / `ContextualRecallMetric` 区分"排得准" —— 调 alpha、换 RRF、接 CE 的 A/B 必需。

**R10. 延迟可观测**：`hybrid_latency_stats()` 的 p50/p95 ring 已有，透出到 `/api/kb/stats` 或进 Langfuse（`llm_trace.py:34` 已有 lazy 接线）。

---

## 2. LLM Wiki 下一步优化

### P0（小改动、高确定性收益）

**W1. claim check 从"诊断"变"治疗"**（`storm_orchestrator.py:_storm_claim_check`）
- 现状：`needs_regenerate` 只进了 `state.meta`，无任何再生动作；`regenerate_prompt`（`claim_checker.py:293`）在 STORM 链路零调用。
- 改动：`needs_regenerate=True` 时对 failed section 定向再生 —— 用现成 `regenerate_prompt` + `draft_section` 重写该章，再走一次 `stitch_and_polish`（引用重映射已幂等）。设最大 1 轮防循环。

**W2. 加厚每章证据**（`storm_draft.py:collect_section_evidence`）
- 现状：Wiki grounding 是全链路最薄的一环 —— 每 query k=2、总 cap 4 条/章，用 `search_chunks`（非 hybrid），没用 rerank。
- 改动：每 query k=2→k=6，总 cap 4→10；换 `search_chunks_hybrid`（`kb_index.py:690`）；候选池调 `rag.rerank_scored(q, candidates, prefer="cross_encoder")`。

**W3. 脚注补页码锚点**（`storm_polish.py:_anchors_from_source_ids`）
- 现状：`cite_pool` 是纯 source_id 字符串，`CitationAnchor(chunk_id=sid, ...)` 没带 page/paragraph —— STORM 文末"引用清单"无页码，与 chat 上下文的 `(p.N, ¶M)` 口径不对齐，引用无法定位。
- 改动：从 `chunk_store` 按 source_id 查回 `page_no`（`DocumentChunk` 有该列），脚注渲染 `[^n]: title (p.3)`。

**W4. claim check 的证据质量**（`storm_orchestrator.py:_pack_to_evidence`）
- 现状：`relevance` 硬编码 0.5、snippet 截 800 字符、无页码 —— 传给 `verify_claims_llm` 的证据本身很弱，LLM 核验输入天花板太低。
- 改动：用 pack 行真实相关度/排序分，snippet 取证据原文而非 summary，带上 page。

### P1（大纲、视角、一致性）

**W5. 大纲从固定模板到数据驱动**（`storm_outline.py:build_deterministic_outline`）
- 现状：6 章模板不管 pack 有没有货都全上；`retrieval_queries` 是 `"xxx 技术要求"` 类 naive 模板；LLM 大纲默认关闭。
- 改动：按 pack 实际内容裁剪章节（无 DOE 数据弱化 `sec_doe_lab`）；`retrieval_queries` 按视角生成差异化 query（成本视角→"硅烷 成本 供应"）；LLM key 可用时默认 `use_llm=True`。

**W6. 视角从展示变为生产力**（`storm_schema.py` + `storm_draft.py`）
- 现状：`DEFAULT_PERSPECTIVES` 5 个中文标签只出现在文首一行字和大纲 prompt 输入，无任何访谈代码。
- 改动（轻量版，不做完整 STORM 访谈循环）：每章起草前为每个视角生成 1–2 个质询问题（如"盐雾工程师会追问什么数据缺口？"），用 `collect_section_evidence` 检索回答，写入该章"开放问题"小节或风险章。

**W7. 长文一致性：术语表 + 跨章矛盾检测**（`storm_draft.py` / `storm_polish.py`）
- 从 pack 抽取规范术语表（材料名、指标名、CAS）注入每章 prompt 的 system 指令，防同物异名；polish 阶段对各章 `extract_claims` 后做跨章 token/数值矛盾扫描（`claim_checker.extract_claims` 已有，按 section 分组复用），矛盾进"论断核验"附录。

**W8. 数字保真二次校验**（`storm_polish.py` 新增函数）
- `_pack_excerpt_for_section` 的"禁止改写数字"只是 prompt 约束。polish 时从成稿抽取数值（已有 `_NUMERIC_IN_TEXT`，`claim_checker.py:300`）与 pack 附录表交叉比对，不一致追加"数值存疑"标记。工业报告场景的高价值校验。

**W9. 审阅闭环**（`api/wiki.py:859` + `wiki/review.py`）
- 现状：`apply_page_review` 已接 API，但审阅结果不回流，闭环断。
- 改动：`human_override` 文本和被驳回 claim 存为项目"审阅语料"，下次同项目生成时做 few-shot 负例注入大纲 prompt；统计各章 claim 通过率，供 W1 定向再生排序。

### P2（评估与工程）

**W10. Wiki 质量门禁**（新增 `backend/tests/test_wiki_eval.py`，扩展 `ci_golden_gate.sh`）
- 引用精确率回归（`scrub` 已保证，防退化）；
- 3–5 个固定 topic 跑 deterministic STORM（离线可跑），`pass_rate` 低于基线 CI 告警；
- 大纲贴合度 LLM judge（可选，与 R8 共用 DeepEval）。

**W11. 跨章证据去重**：同一 source_id 被多章引用时每章独立检索、snippet 重复进上下文。orchestrator 层加全局证据池去重（按 `chunk_id`），省上下文预算。

**W12. `extract_claims` 按章采样**（`claim_checker.py:44`）：现在全篇截前 20 条，长文后半核验不足。改为每章独立提取（上限 8 条/章），与 `SectionDraft` 粒度对齐。

---

## 3. 建议实施顺序

1. **R1**（默认 embedding + 重建索引）—— 所有检索优化的地基；不做它，R2/R4/R9 的 A/B 都在小模型上测，结论不可迁移。
2. **W2 + W3**（Wiki 证据加厚 + 脚注页码）—— Wiki grounding 最薄的两处，小改动。
3. **R2**（chat 接 CE）+ **R3**（SimHash 去重）—— 排序质量的两个确定性增益。
4. **W1 + W4**（claim 治疗 + 证据质量）—— 把已投入的 claim check 变成闭环。
5. **R4**（RRF A/B）+ **W5**（数据驱动大纲）—— 需要 R1 落地后的基准才能做 A/B。
6. **W6/W7/W8/W9**（视角、一致性、数字校验、审阅闭环）—— Wiki 工业报告能力的深水区。
7. **R7/R8/R9/R10/W10/W11/W12**（向量库、评估门禁、可观测）—— 规模化和工程化，按需排期。

每一步改后跑全量测试保持全绿，再进入下一步。
