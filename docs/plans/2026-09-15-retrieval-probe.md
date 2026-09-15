# 维 3：检索探针（Retrieval Probe）独立方案

> 状态：**决策已锁定并落地实现（同里程碑含 Golden 批跑）**  
> 日期：2026-09-15  
> 来源：Yuxi 借鉴评估维 3；对照仓库 Yuxi v0.7.3（`vendor/Yuxi`，MIT）与 FormuMind 现网  
> 范围：KB chunk 召回可观测性（关键词 / 混合 / 可选 LLM 重排）；不含 Wiki FTS、结构化学检索、文献外网流式检索的全量统一
>
> **实现入口**：Knowledge Hub →「检索探针」；API：`POST /api/kb/query-test`、`GET /api/kb/golden-questions`、`POST /api/kb/golden-eval/run`

---

## 0. 结论摘要

FormuMind **已有** Settings → 依赖管理里的迷你 KB 探针（`DependencyManager`），以及后端 `GET /api/kb/search`、`POST /api/kb/hybrid-search`、`llm_rerank`、golden_eval。  
**缺的是**「研发人员能一眼看出召回为什么好/坏」的调优工作台：

| 缺口 | 现网事实 |
|------|----------|
| Hybrid **不算分给前端** | `hybrid_search()` 内部算 BM25/cosine/combined，构造 `DocumentChunkResponse` 时丢弃 `_score` |
| 无 **初筛 → 重排前后** 对比 | `llm_rerank` 只挂在 literature/chat/research，不挂 KB 探针路径 |
| Hybrid **无 project 作用域** | `search_chunks` 支持 `project_id`；hybrid 扫全局 `all_chunks` |
| 探针入口 **埋在依赖管理** | 无分数表、无 alpha/top_k、不传 `activeProjectId` |
| 无模式切换 | Hybrid 仅 `alpha` 线性融合，无 vector-only / keyword-only 探针模式 |

**本方案目标**：用最小后端契约扩展 + 一个正式探针面板，让 3～5 人团队对「硅烷偶联剂 / 磷化液」等专业 Query 做多路召回调优，**不**引入 Milvus、不另立向量 SSOT、不移植 Yuxi Vue 组件。

**非目标（本迭代不做）**：

- 移植 Yuxi `KnowledgeEvaluationWorkspace` 全套 benchmark 产品  
- Wiki 页面搜索、分子结构检索与 KB 探针强行合一  
- 多租户角色门禁（现网无 admin role；维持 token 鉴权即可）  
- 用 LangGraph/ARQ 重写检索链路  

---

## 1. 现状盘点（代码事实）

### 1.1 后端

| 资产 | 路径 | 说明 |
|------|------|------|
| 关键词检索 | `backend/app/api/kb.py` → `GET /api/kb/search` | `q,k,project_id?` → `{ results: Evidence[] }`，含 `relevance` |
| 混合检索 | 同文件 → `POST /api/kb/hybrid-search` | `{ query, top_k, alpha }` → **裸** `DocumentChunkResponse[]`，**无分** |
| Hybrid 实现 | `backend/app/services/hybrid_search.py` | BM25×α + cosine×(1-α)；分在 L152 丢弃 |
| LLM 重排 | `backend/app/services/rag.py` → `llm_rerank` | 重排 `Evidence`，失败回退 `candidates[:k]` |
| 重排开关 | `search_rerank_enabled` 等（`env_flags` / retrieval 组） | 当前服务文献流/chat，**不**服务 KB 探针 |
| Golden | `backend/tests/golden_eval_dataset.py` + `test_golden_eval.py` | 7 题 keyword-hit@3；无 MRR/nDCG API |

### 1.2 前端

| 资产 | 路径 | 说明 |
|------|------|------|
| 迷你探针 | `frontend/src/components/DependencyManager.tsx`（`kb-probe-panel`） | 关键词有 `relevance`；hybrid 无分；固定 k/α |
| API 客户端 | `frontend/src/api.ts` → `kbSearch` / `kbHybridSearch` | 与上表一致 |
| 项目上下文 | `activeProjectId`（`projectSlice`） | Hub 已用；探针未用 |
| Knowledge Hub | `knowledge-hub/KnowledgeHubModal.tsx` | materials / wiki / graph / reports — **无 retrieval 页签** |

### 1.3 Yuxi 只读参照（借交互，不借栈）

- UI：`vendor/Yuxi/web/src/components/QuerySection.vue`、`SearchConfigPanel.vue`、`KbResultGroupedList.vue`  
- API 叙事：`POST .../query-test`，命中带 `score` / `rerank_score`  
- 文档：`vendor/Yuxi/docs/intro/evaluation.md`  

---

## 2. 产品形态

### 2.1 入口（二选一，推荐 A）

| 方案 | 做法 | 取舍 |
|------|------|------|
| **A（推荐）** | Knowledge Hub 新增页签 **「检索探针」** | 研发主路径；与 wiki/资料同屏；不把调优埋进依赖管理 |
| B | Settings → 独立「检索」页，升级并迁出 `DependencyManager` 迷你面板 | 偏运维；与 Hub 项目上下文弱 |

MVP 采用 **A**；DependencyManager 内旧探针 **保留为瘦入口**（链到 Hub 或保留最小冒烟），避免破坏现有 `data-testid` 运维习惯。

### 2.2 界面线框（单一面板）

```
┌─ Knowledge Hub · 检索探针 ─────────────────────────────────┐
│ Query [________________________]  [运行]                    │
│ 作用域: (● 当前项目  ○ 项目+全局  ○ 全局)   project: {id}   │
│ 模式:   [关键词] [混合] [混合+重排]                          │
│ top_k: [10]   alpha(BM25): [0.3]   重排: 跟 env 或本页覆盖   │
├────────────────────────────────────────────────────────────┤
│ # │ source / heading │ bm25 │ cosine │ hybrid │ rerank │ Δ │
│ 1 │ …                │ 0.82 │ 0.41   │ 0.53   │ 0.91   │↑ │
│ 2 │ …                │ …    │ …      │ …      │ …      │  │
├────────────────────────────────────────────────────────────┤
│ 选中行：snippet 全文 + 打开 SourceDetail（复用现有 Modal）   │
│ [原始 JSON] 开关                                            │
└────────────────────────────────────────────────────────────┘
```

化学默认样例 Query（面板底部 chips，可一点即跑）：

- `硅烷偶联剂`  
- `磷化液`  
- `钝化膜 铬酸盐`  
- `水性环氧 盐雾`  

### 2.3 成功标准（MVP）

1. 同一 Query，表格同时可见 **bm25_score / cosine_score / hybrid_score**（混合模式）。  
2. 「混合+重排」开启时，可见 **rerank_score** 与相对 hybrid 的名次变化（Δ）。  
3. 支持 `project_id` + `include_global`（与 Hub sources 语义对齐）。  
4. 不改默认生产检索行为：探针走 **专用 debug 端点或显式 `debug=true`**，避免悄悄改变 `/hybrid-search` 调用方契约。  
5. 自动化：后端单测断言分数字段存在且排序与 hybrid 一致；前端组件测至少覆盖空 Query / 有结果表头。

---

## 3. API 契约（核心设计）

### 3.1 推荐：新增专用端点（避免破坏现有 hybrid 消费者）

```
POST /api/kb/query-test
```

**Request**

```json
{
  "query": "硅烷偶联剂",
  "mode": "keyword | hybrid | hybrid_rerank",
  "top_k": 10,
  "alpha": 0.3,
  "project_id": "optional-uuid",
  "include_global": true,
  "rerank": null
}
```

- `rerank`: `null` = 跟随 `search_rerank_enabled`（仅 `hybrid_rerank` 模式有意义）；`true`/`false` = 本请求覆盖。  
- `mode=keyword`：复用 `search_chunks` 路径，映射为统一 hit 形状。  
- `mode=hybrid`：复用 `hybrid_search` 内核，**保留分数字段**。  
- `mode=hybrid_rerank`：hybrid 取候选（建议 `max(top_k, search_rerank_llm_batch` 上限内）→ 转 `Evidence` → `llm_rerank` → 回填 `rerank_score`。

**Response**

```json
{
  "query": "...",
  "mode": "hybrid_rerank",
  "params": { "top_k": 10, "alpha": 0.3, "project_id": "...", "include_global": true, "rerank_applied": true },
  "vector_mode": "semantic",
  "elapsed_ms": 123,
  "hits": [
    {
      "rank": 1,
      "chunk_id": "...",
      "source_id": "...",
      "ord": 0,
      "title": "heading or source title",
      "snippet": "…≤400 chars…",
      "text": "full chunk text optional or omit in list",
      "bm25_score": 0.82,
      "cosine_score": 0.41,
      "hybrid_score": 0.53,
      "relevance": 0.53,
      "rerank_score": 0.91,
      "rank_before_rerank": 4,
      "meta": {}
    }
  ]
}
```

字段规则：

- `keyword`：只保证 `relevance`（= 现 `Evidence.relevance`）；bm25/cosine/hybrid 可为 `null`。  
- `hybrid`：保证三分 + `hybrid_score` 降序；`rerank_*` 为 `null`。  
- `hybrid_rerank`：保证 `rerank_score` 与 `rank_before_rerank`；最终列表按 rerank 排序，`rank` 为重排后名次。

### 3.2 对现有 `/hybrid-search` 的处理

**默认不变**（仍返回无分的 chunk 列表，兼容 golden_eval / 旧客户端）。  
可选后续（非 MVP）：`?debug_scores=1` 或响应包一层 — **本方案不依赖**，以免拖垮评审。

### 3.3 服务层改造要点

1. 从 `hybrid_search` 抽出内核，例如：

   ```python
   def hybrid_search_scored(...) -> list[ScoredChunk]
   # ScoredChunk: chunk + bm25 + cosine + hybrid
   def hybrid_search(...) -> list[DocumentChunkResponse]  # 薄包装，行为不变
   ```

2. `all_chunks` 增加与 sources 一致的 `project_id` / `include_global`（keyword 路径已有 project；hybrid 补齐）。  
3. `query_test` 编排：scored hybrid →（可选）Evidence 映射 → `llm_rerank` → 组装 `hits`。  
4. 超时/降级：重排失败时 `rerank_applied=false`，hits 退回 hybrid 序，并在 `params` 或顶层 `warning` 说明。

### 3.4 明确不做的 API

- 不新增第二套向量库配置。  
- 不把 Wiki `/api/wiki/search` 塞进同一 hits 表（可在 UI 注脚「Wiki 见 Hub·Wiki」）。  
- 不做持久化「评测运行历史」表（留给 P1）。

---

## 4. 前端落地

### 4.1 文件建议

| 动作 | 路径 |
|------|------|
| 新增面板 | `frontend/src/components/knowledge-hub/RetrievalProbePanel.tsx` |
| Hub 页签 | `KnowledgeHubModal.tsx` + store `knowledgeHubTab` 联合类型加 `"retrieval"` |
| API | `frontend/src/api.ts` → `kbQueryTest(body)` |
| 类型 | hits / response 与后端对齐 |
| 测试 | `RetrievalProbePanel.test.tsx`（mock api） |
| 瘦身提示 | `DependencyManager` 探针区加一行「完整探针见知识中枢 → 检索探针」 |

### 4.2 UX 细则

- 默认作用域：有 `activeProjectId` → 项目+全局；否则全局。  
- 运行中禁用按钮；错误用现有 toast/报告条。  
- 分数列等宽等宽字体；`null` 显示 `—`。  
- Δ：重排后名次相对 `rank_before_rerank` 的升降箭头（↑ 变好）。  
- 点击行打开既有 `SourceDetailModal` / chunk 预览（若现成钩子不够，MVP 可先展开 snippet）。

### 4.3 不移植

Yuxi 的 Ant Design、`query-params` 动态表单生成、manage-gated sample question 生成器 — FormuMind 用固定化学 chips + 简单受控表单即可。

---

## 5. 实施切片

### P0（MVP，建议 1 个 PR）

1. Backend：`ScoredChunk` + `hybrid_search_scored`；`hybrid_search` 行为回归不变。  
2. Backend：`POST /api/kb/query-test` + project 作用域。  
3. Backend：`mode=hybrid_rerank` 接线 `llm_rerank`（可 feature 内默认跟随 env）。  
4. Tests：扩展 `test_hybrid_search.py`；新增 `test_kb_query_test.py`（分数单调、keyword 映射、rerank 降级）。  
5. Frontend：Hub「检索探针」面板 + `kbQueryTest`。  
6. 文档：本方案状态改为「实施中 / 已落地」备注。

### P1（紧随，可选第二 PR）

1. Golden 批跑按钮：读 `golden_eval_dataset` 同源 JSON（或导出为 `data/golden_retrieval.json`），逐条调 `query-test`，表格 pass/fail（仍用 keyword-hit@k，不发明虚假 nDCG）。  
2. DependencyManager 迷你探针改为调用 `query-test` 或仅保留链接。  
3. 将常用 Query chips 可配置化（按 `ProductDomain`）。

### P2（明确延后）

- 持久化评测 run、团队共享数据集、与 Yuxi eval workspace 对标。  
- vector-only / bm25-only 作为一等 `mode`（MVP 可用 α=0 / α=1 近似，并在 UI 标注）。  
- Admin RBAC。

---

## 6. 测试与验收

| 层级 | 内容 |
|------|------|
| 单测 | scored hybrid 排序 == 旧 hybrid 的 chunk id 序；query-test 响应含分；project 过滤不漏全局策略 |
| 单测 | rerank 关闭/失败 → `rerank_applied=false`，无 500 |
| 前端测 | 渲染表头；mock 三模式 hits |
| 手动 | Hub 打开探针，跑「硅烷偶联剂」「磷化液」，确认三分可见；开重排看 Δ |
| 回归 | 现有 `test_golden_eval` / `test_hybrid_search` / Hub 其他页签无破坏 |

---

## 7. 风险与约束

| 风险 | 缓解 |
|------|------|
| `llm_rerank` 慢/贵 | 探针默认 `hybrid`；重排需显式选模式；遵守 `search_rerank_llm_batch` |
| 扫全库 hybrid 在大语料上慢 | 沿用 `kb_search_scan_limit`；UI 展示 `elapsed_ms` + `vector_mode` |
| 改 hybrid 签名破坏调用方 | 内核抽出 + 旧函数薄包装；专用 `query-test` |
| 与 Wiki/文献检索混淆 | 面板标题写清「KB Chunk 召回」；脚注指向其他入口 |
| Claims / 配方门禁 | 本探针只读检索，不写 ELN、不触发 recommend/DOE |

---

## 8. 工作量粗估

| 项 | 量级 |
|----|------|
| Backend scored + query-test + tests | 0.5–1 人日 |
| Frontend Hub 面板 + api + tests | 0.5–1 人日 |
| 联调 / 化学样例验收 | 0.5 人日 |
| **P0 合计** | **约 1.5–2.5 人日** |

---

## 9. 决策清单（已锁定 2026-09-15）

- [x] 入口选 **Knowledge Hub「检索探针」**
- [x] 同意 **新端点** `/api/kb/query-test`，保持 `/hybrid-search` 不变
- [x] MVP 包含 **hybrid_rerank**
- [x] 暂不拆独立 vector/keyword mode（可用 α=0/1 近似）
- [x] **golden 批跑进同一里程碑**

---

## 10. 参考路径速查

**FormuMind**

- `backend/app/api/kb.py`  
- `backend/app/services/hybrid_search.py`  
- `backend/app/services/rag.py`（`llm_rerank`）  
- `backend/app/services/kb_index.py`（`search_chunks`）  
- `frontend/src/components/DependencyManager.tsx`  
- `frontend/src/components/knowledge-hub/KnowledgeHubModal.tsx`  
- `frontend/src/api.ts`  
- `backend/tests/test_hybrid_search.py`、`golden_eval_dataset.py`  

**Yuxi（只读）**

- `vendor/Yuxi/web/src/components/QuerySection.vue`  
- `vendor/Yuxi/backend/server/routers/knowledge_router.py`（query-test）  
