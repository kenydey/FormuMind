# P3.2 — Evidence 一键入库全文 → 挂接 P3.1 实施例草稿

状态：**已实施**（2026-09-09）  
前置：P3.1 已入库全文实施例草稿（[`2026-09-09-embodiment-fulltext-drafts.md`](./2026-09-09-embodiment-fulltext-drafts.md)）  
相关：SureChEMBL P0–P3 · `kb_ingest.py` · `fulltext_fetcher.py`

## 1. 决策摘要

| 做 | 不做 |
|----|------|
| 检索 Evidence **用户点选**「入库全文」→ 写入 `SourceDocument` + chunks | 批量自动抽全库配方 |
| 修复 SureChEMBL / 带连字符公开号 **不可 classify** 导致无法入库 | 新 OpenAlex/EPO 检索 Tab |
| 公开号归一 + `origin_url` 去重（compact / hyphen / Google URL） | 把「入库图谱」(KG) 与「入库全文」混成一个按钮 |
| 入库成功后 UI 能解析 `source_id` → 直接走 P3.1 提取 | 入库时静默 confirm 草稿 / 写生产池 |
| 后台自动 ingest 队列也吃到归一后的 patent id | Slice E 散文抽取 |

一句话：**让用户能从检索行点到「已有全文」，从而点亮 P3.1「提取实施例草稿」。**

---

## 2. 背景与问题

P3.1 已要求：只有 **eligible KB 文档**才能抽真实配比。但现状漏斗断裂：

1. SureChEMBL Evidence 的 `identifier` 是 `CN-104789083-B`（SCPN 连字符）。
2. [`fulltext_fetcher.classify`](../../backend/app/services/fulltext_fetcher.py) 的 `_PATENT_RE` **不含连字符** → 返回 `None` → **永不进入** `kb_ingest` 队列。
3. Evidence 行只有「入库图谱」（KG chemistry），**没有**「入库全文」按钮。
4. `origin_url` 用裸 `identifier` 精确匹配去重，compact / 连字符 / URL 三种形态会重复下载或挂不上 `source_id`。
5. UI 仅用 `kbIngest.docs[].identifier === e.identifier` 映射徽章；即使别处入库了 compact 号，SureChEMBL 行也挂不上。

因此用户几乎点不到 P3.1 的真实配比路径。

---

## 3. 目标

### 3.1 功能

1. **公开号归一工具**（单点真相）  
   - 输入任意：`CN-104789083-B` / `CN104789083B` / Google Patents URL  
   - 输出：`compact`（如 `CN104789083B`）、可选 `hyphenated` SCPN 猜解、`office`  
   - 落点：抽到 `backend/app/services/patent_ids.py`（或从 `embodiment_drafts.normalize_patent_pub` 提升为共享模块），供 fetcher / ingest / UI 映射共用。

2. **`classify` / fetch 修复**  
   - 归一后再匹配 `_PATENT_RE`  
   - `source == "surechembl"` 且有 `url` 含 `patents.google.com` → 当作 patent  
   - `_fetch_patent_text` **始终用 compact** 调 `fetch_patent_text`

3. **Dedup 强化**  
   - `find_by_origin_url` 查询时尝试：compact、hyphenated、Google Patents URL、原始 identifier  
   - `_persist_fulltext` 写入的 `origin_url` **优先存 compact**（另在 aliases/guide 可记 SCPN）

4. **按条入库 API**  
   - `POST /api/kb/ingest-evidence`  
   ```json
   {
     "identifier": "CN-104789083-B",
     "title": "...",
     "url": "https://patents.google.com/patent/CN104789083B",
     "source": "surechembl",
     "project_id": null
   }
   ```  
   - 行为：构造 `Evidence` → 走现有 `kb_ingest` 单篇路径（或同步短路径）  
   - 返回：`{ task_id?, status_url?, source_id?, status, reason? }`  
   - 已存在则 `status=skipped` + 已有 `source_id`（不重复下载）

5. **UI（SourcesPanel Evidence 行）**  
   - 对 `patent*` / `surechembl` / 可 OA 的 `literature`（有 DOI/arxiv 或 `is_oa`）：显示 **入库全文**  
   - 与「入库图谱」并列、语义分离  
   - 状态机：未入库 → 入库中 → 已入库 / 失败（复用 `KbDocBadge` 或行内文案）  
   - `source_id` 解析：`kbIngest` **或** `kbDocs` 经归一 `origin_url` 匹配  
   - 已入库且 P3.1 eligible → 显示/启用「提取实施例草稿」（全文路径）；SureChEMBL 无全文仍可占位草稿

### 3.2 非目标

- 改变 P3.1 红线（confirm 仍 pending only）  
- 自动对所有检索结果强制入库（后台 auto 逻辑可照旧，但本切片焦点是**用户点选**）  
- 入库失败时伪造全文  
- Slice E 散文配比  

---

## 4. 架构

```text
Evidence row (SureChEMBL / patent / OA lit)
    │ 用户点击「入库全文」
    ▼
POST /api/kb/ingest-evidence
    │ normalize_patent_id / DOI
    │ classify → patent|literature|web
    │ dedup by origin aliases
    ▼
fulltext_fetcher + parse + kb_index
    │
    ▼
SourceDocument (origin_url=compact) + chunks
    │ SSE / poll → frontend source_id
    ▼
P3.1 eligibility → extract-embodiment-draft
    (surechembl_hint=true when source=surechembl)
```

与「入库图谱」关系：

| 按钮 | 写入 |
|------|------|
| 入库图谱 | KG `patent:scpn` / `chem:surechembl`（现网） |
| 入库全文 | `SourceDocument` + chunks（本切片） |
| 提取实施例草稿 | P3.1 人审草稿（有全文优先真实 wt%） |

三者独立，可顺序点。

---

## 5. 实现切片

### Slice A — 归一 + classify/fetch/dedup（~1–2 天，阻塞项）

- [x] `patent_ids.py`：`normalize_patent_pub` / `patent_id_aliases` / `google_patents_url`
- [x] `fulltext_fetcher.classify` 先 normalize；SureChEMBL 特例
- [x] `_fetch_patent_text` 用 compact
- [x] `_persist_fulltext` + `SourceStore.find_by_origin_url`（或 wrapper）多别名查找
- [x] 单测：`CN-104789083-B` → kind=`patent`；dedup 连字符与 compact 命中同一行

### Slice B — `POST /api/kb/ingest-evidence`（~1–2 天）

- [x] 路由挂在 [`backend/app/api/kb.py`](../../backend/app/api/kb.py)（或 ingest）
- [x] 复用 `kb_ingest._fetch_one` / `_index_one` 或 `ingest_evidence_docs([ev], ...)`
- [x] 支持同步完成（单篇、超时内）**或**返回 `task_id` + 与现网一致的 SSE（优先：能复用 `dispatch_kb_ingest` 单元素列表）
- [x] 失败返回可读 `reason`（无 OA / 超时 / 解析空）
- [x] pytest：mock fetch 成功 → 有 `source_id`；二次调用 → skipped

### Slice C — SourcesPanel UX（~1–2 天）

- [x] Evidence 行「入库全文」按钮 + busy/badge
- [x] `api.ingestEvidence(...)` + 接入 `trackKbIngest` 或短轮询
- [x] `resolveSourceId(evidence, kbIngest, kbDocs)` 归一匹配
- [x] SureChEMBL：有 `source_id`+eligible → `extractEmbodimentDraft({ surechembl_hint: true })`；否则占位 + tooltip
- [x] vitest：连字符 id 点击入库后出现已入库态；eligible 后出现全文提取

### Slice D — 自动队列顺带修复（~0.5 天，可与 A 同 PR）

- [x] `select_ingest_targets` 使用同一 classify → 后台 auto ingest 也能收 SureChEMBL 专利行（仍受 `kb_ingest_auto` / 主题预筛 / max_docs 约束）
- [x] 文档注明：auto 与手动按钮并存

---

## 6. API 契约（建议）

### `POST /api/kb/ingest-evidence`

请求：

| 字段 | 必填 | 说明 |
|------|------|------|
| identifier | 是 | Evidence.identifier |
| title | 否 | |
| url | 否 | Google Patents / OA PDF 提示 |
| url_alt | 否 | |
| source | 否 | `surechembl` / `patent` / … |
| project_id | 否 | |
| assignee / pub_date | 否 | 透传 metadata |

响应：

```json
{
  "ok": true,
  "status": "indexed|skipped|queued|failed",
  "source_id": "uuid-or-null",
  "task_id": "optional",
  "status_url": "optional",
  "canonical_id": "CN104789083B",
  "reason": null
}
```

---

## 7. 红线

1. 入库全文 ≠ 确认配方；不写生产池 / leaderboard。  
2. 「入库图谱」行为不变。  
3. 无全文 / 抓取失败时不得把占位 Chemistry 标成 `amount_source=table`。  
4. 不在本 API 内触发 P3.1 confirm。

---

## 8. 测试与验收

### 自动化

- [x] `CN-104789083-B` classify → `patent`
- [x] compact / hyphen / URL 去重同一 `source_id`
- [x] ingest-evidence mock 成功 → indexed + source_id
- [x] 二次 ingest → skipped
- [x] vitest：按钮显隐、映射 source_id、SureChEMBL 全文提取路径

### 手工冒烟（DoD）

1. 检索 SureChEMBL → 点「入库全文」→ 徽章「已入库」  
2. 同一行可点「提取实施例草稿」→ Modal 出现；若解析含表则 `amount_source=table`  
3. 再点「入库全文」→ skipped，不双份文档  
4. 「入库图谱」仍可独立使用  

---

## 9. 工期与优先级

| 切片 | 估时 | 优先级 |
|------|------|--------|
| A 归一 + classify/dedup | 1–2 天 | P0 |
| B ingest-evidence API | 1–2 天 | P0 |
| C SourcesPanel UX | 1–2 天 | P0 |
| D auto 队列顺带 | 0.5 天 | P1 |

合计约 **3–5 天**（单人）。

---

## 10. 文件落点（预期）

| 层 | 路径 |
|----|------|
| 公开号归一 | `backend/app/services/patent_ids.py`（新） |
| classify/fetch | `backend/app/services/fulltext_fetcher.py` |
| 单篇入库 | `backend/app/services/kb_ingest.py` + `backend/app/api/kb.py` |
| UI | `frontend/src/components/SourcesPanel.tsx` · `frontend/src/api.ts` · `frontend/src/utils/patentIds.ts` |
| 测试 | `backend/tests/test_kb_ingest_evidence.py` · vitest SourcesPanel.p32 |
| 计划 | 本文 |

---

## 11. 状态

- [x] 产品方向确认：下一优先 = Evidence→入库全文→P3.1  
- [x] 实施计划成文  
- [x] Slice A–D 实施  
- [ ] 冒烟 DoD（人工）  

**实施说明**：手动「入库全文」走同步 `ingest_single_evidence`（绕过 topic 预筛与 `kb_ingest_auto`）；后台 auto 队列仍受开关/预筛约束，但已共享修复后的 `classify`。
