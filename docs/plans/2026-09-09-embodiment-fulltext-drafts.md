# P3.1 — 已入库全文实施例配方草稿（人审 · 真实比重优先）

状态：**已实现**（2026-09-09）  
前置：SureChEMBL P0–P3（lookup · substitutes · content search · SCHEMBL KG/占位草稿）  
后续漏斗：[`2026-09-09-kb-ingest-evidence-fulltext.md`](./2026-09-09-kb-ingest-evidence-fulltext.md)（P3.2 Evidence→入库全文）

## 1. 决策摘要（产品已确认）

| 做 | 不做 |
|----|------|
| **仅**针对 **已入库** 且 **已有全文解析** 的专利 / 文献 / OA PDF | 对 **仅摘要**、无 `SourceDocument` 全文、或解析失败的检索 Evidence **不提供**「提取实施例草稿」 |
| 表格/OCR/HTML 抽出的 **真实或可归一比重**（wt% / 重量份） | 检索热路径自动批量抽配方 |
| 用户点选触发 + 人审 Modal + confirm 后 KG + `force_pending` | 静默写入生产配方池 / leaderboard / `RAW_MATERIALS` upsert |
| SureChEMBL 化学列表仍可走 **占位均分**（诚实 fallback），但 **优先**在「同一公开号已入库全文」时走真实比重 | 把 OpenAlex/arXiv **摘要行**伪装成可抽配比的实施例 |
| Unpaywall / EPO / USPTO 作为 **入库与全文管道** | 新增独立「Unpaywall Tab / 无全文抽配方」 |

一句话：**先有全文，才有草稿；先有人审，才进 pending。**

---

## 2. 背景与问题

P3（SureChEMBL）已交付：

- KG：`patent:scpn:{doc}` / `chem:surechembl:{id}` + `appears_in`/`claimed_in`
- 草稿：来自 `document_chemistry` → **等权 wt% 占位** + `needs_review`

局限：

1. SureChEMBL chemistry **不含**实施例配比 → 均分是正确降级，不是终态。
2. FormuMind 已有 fulltext 入库（`kb_ingest` / `SourceDocument` / chunks）与表格视觉抽取（`VisionFormulation`），但 **未接到**人审 Formulation 草稿 UX。
3. 若对「只有摘要的 OpenAlex/arXiv/专利 Evidence」开放抽取，必然幻觉配比或空转 OCR，违反红线。

因此 P3.1 把范围收窄为：**知识库里已经解析成功的文档**。

---

## 3. 目标

### 3.1 功能目标

1. **资格门禁（硬）**  
   仅当文档满足全部条件时，UI 才显示「提取实施例草稿」：
   - 存在 `SourceDocument`（已入库）
   - `extraction_status ∈ {ok, degraded}`（或等价「有可用正文」）
   - `raw_text_chars`（或 chunk 总长）≥ 阈值（建议 ≥ 500；可配置）
   - 有至少 1 条 `document_chunks` **或** 非空 `full_text`

2. **真实比重优先**  
   抽取优先级：
   1. 结构化表格（MinerU HTML 表 / 页图 `VisionFormulation`）→ `concentration` + `unit` → 归一 `weight_pct`
   2. （可选，低置信）正文「重量份 / wt% / phr」规则 + LLM，必须带证据句
   3. 仍无量表 → **占位均分** 或 `amount_unknown`，文案标明 `amount_source=placeholder`

3. **统一人审契约**（继承 P3）  
   - Draft：`needs_review=true`，`origin` ∈ `patent_fulltext` | `literature_fulltext` | `oa_pdf` | `surechembl` | `surechembl+fulltext`
   - Confirm：KG + `propose_material(..., force_pending=True)`；`promoted_to_pool=false`
   - 多实施例 → **多草稿**（Example 1/2/…），禁止合成一张假总表

4. **KG 扩展（与草稿解耦可并行）**  
   - 专利：`patent:{office}:{pub}`（归一公开号）；可与 `patent:scpn:` `same_as`
   - 文献：`paper:openalex:{id}` / `paper:doi:{doi}` / `paper:arxiv:{id}`（仅对已入库全文文档建实体）
   - 边：成分 → 文档 `appears_in`；草稿 form → 文档；`has_ingredient` 带 **非占位** `weight_pct`（若有）

### 3.2 非目标

- 检索结果行上「一键抽配方」但文档尚未入库 / 仅摘要
- 点击时现场下载 PDF + 长耗时 OCR（应引导用户先走既有 **入库 / KB ingest**；P3.1 只读已解析产物）
- Unpaywall / EPO / USPTO 新检索 Tab
- 散文实施例无证据的「精确到小数」配比
- 自动推进推荐配方池 / DOE 因子自动采纳

---

## 4. 资格与数据契约

### 4.1 文档资格 API（新建或挂在 kb）

`GET /api/kb/sources/{source_id}/embodiment-eligibility`  
或批量：`POST /api/formulations/embodiment-eligibility` `{ source_ids: [] }`

返回示例：

```json
{
  "source_id": "...",
  "eligible": true,
  "reason": null,
  "raw_text_chars": 42000,
  "extraction_status": "ok",
  "chunk_count": 86,
  "source_kind": "patent",
  "has_table_signal": true
}
```

`eligible=false` 时 `reason` 枚举：

| reason | 含义 | UI |
|--------|------|-----|
| `not_ingested` | 无 SourceDocument | 不显示按钮；可提示「先入库」 |
| `abstract_only` | 仅摘要/无全文 | 不显示 |
| `extract_failed` | `extraction_status=failed` | 不显示或「解析失败」 |
| `too_short` | 正文过短 | 不显示 |
| `no_chunks` | 无切块且无 full_text | 不显示 |

### 4.2 Draft 模型（相对 P3 增量字段）

```text
FormulationEmbodimentDraft
  status: draft
  needs_review: true
  origin: patent_fulltext | literature_fulltext | oa_pdf | surechembl | surechembl+fulltext
  source_id: str                 # KB SourceDocument.id（必填，P3.1）
  doc_id / identifier: str       # 公开号 / DOI / arXiv id
  amount_source: table | prose | placeholder | mixed
  embodiments: [
    {
      label: "Example 1",
      page_hint: int | null,
      ingredients: [{ name, role, weight_pct, unit_raw, confidence, evidence_span }],
      warnings: []
    }
  ]
  formulation: { ... 与现有 Formulation 对齐的主选实施例 ... }
```

Confirm 请求必须带 `source_id` + `needs_review` + 允许的 `origin`，防止伪造生产写入。

### 4.3 比重归一规则

| 原始 unit | 处理 |
|-----------|------|
| `wt%` / `%` / `质量分数` | 直接用；总和 ∉ [95,105] → warning |
| `重量份` / `parts` / `phr` | `weight_pct = 100 * parts / Σparts` |
| `g` / `g/L`（配方表语境） | 仅当同行可加总时归一；否则 `amount_uncertain` |
| 缺失 | 该组分 `weight_pct=null` 或整表 fallback placeholder |

---

## 5. 架构

```text
SourcesPanel / 知识库文档行
    │ 仅 eligible=true 显示「提取实施例草稿」
    ▼
POST /api/formulations/extract-embodiment-draft
    { source_id }
    │
    ├─ load SourceDocument + chunks（禁止对未入库 id 现场抓取）
    ├─ detect tables / reuse cached vision extractions if any
    ├─ extract_formulations_from_fulltext(...)
    │     table → VisionFormulation / HTML table
    │     prose  → optional low-confidence
    │     none   → placeholder + warnings
    └─ return Draft（不写 DB）

SurechemblDraftModal → 泛化为 EmbodimentDraftModal
    │ 人工确认入库
    ▼
POST /api/formulations/confirm-embodiment-draft
    { draft }
    ├─ KG upsert（patent/paper + chem + has_ingredient）
    ├─ propose_material(..., force_pending=True)
    └─ promoted_to_pool: false
```

**与 SureChEMBL P3 关系**

| 场景 | 行为 |
|------|------|
| 仅有 SCHEMBL Evidence，**未**入库全文 | 保持现网：化学列表占位草稿（`/api/surechembl/...`）或灰显「需先入库全文以取真实比重」 |
| SCHEMBL 公开号对应文档 **已入库全文** | P3.1 优先：`origin=surechembl+fulltext`，真实比重；chemistry 作别名/补充 |
| 普通专利/OA 文献已入库 | 仅走 `/api/formulations/extract-embodiment-draft` |

渐进迁移：先加通用 API + UI 门禁；SureChEMBL 专用路由可薄封装转调，避免双逻辑分叉。

---

## 6. 实现切片

### Slice A — 资格门禁 + API 骨架（约 2–3 天）

- [x] `embodiment_eligibility(source_id)` 服务 + 单测
- [x] `POST /api/formulations/extract-embodiment-draft`
- [x] `POST /api/formulations/confirm-embodiment-draft`（复用 P3 confirm 红线）
- [x] SourcesPanel **知识库文档行**：仅 `eligible` 显示按钮

### Slice B — 表格真实比重（约 3–5 天，核心价值）

- [x] 从 chunks / 存档 markdown 中识别表格块
- [x] markdown/HTML 表 → 归一 `weight_pct`（wt% / 重量份）
- [x] unit 归一 + 多实施例拆分
- [x] Draft.amount_source=`table`；confirm 写入 `has_ingredient.metadata.weight_pct` 且 `placeholder_amount=false`
- [x] 无表 → 明确 fallback，不假装

### Slice C — UX 与 SureChEMBL 合流（约 2 天）

- [x] `EmbodimentDraftModal`（泛化 SureChEMBL modal）
- [x] 检索 Evidence 若已映射 `source_id` 且 eligible → 全文提取
- [x] SureChEMBL：有全文则真实比重；无全文保留占位并文案区分

### Slice D — KG 文献/专利实体归一（约 2–3 天，可并行）

- [x] 公开号 normalize（去连字符、国家码）
- [x] `paper:*` / `patent:{office}:{pub}` 实体（确认时写入）
- [x] `same_as` 别名连接 `patent:scpn:` 与归一专利实体

### Slice E —（可选后续）低置信散文份数

- [ ] 仅在 Slice B 上线后；默认关；必须 evidence_span；置信度阈值以下不得标为 table 级精确

---

## 7. UI 行为细则

| 位置 | 条件 | 操作 |
|------|------|------|
| 知识库文档列表 | `eligible` | 「提取实施例草稿」 |
| 检索 Evidence | 已关联 `source_id` 且 `eligible` | 同上 |
| 检索 Evidence | 未入库 / 仅摘要 | **无**抽草稿按钮；可保留「入库」类既有动作 |
| SureChEMBL Evidence | 未入库全文 | 可选保留 P3「占位草稿」；推荐 Tooltip：「入库全文后可提取真实比重」 |
| Modal | 任意 | 琥珀警告：人审闸；确认后不进生产池 |

---

## 8. 与各资料源的关系（澄清）

| 源 | 在本计划中的角色 |
|----|------------------|
| EPO / USPTO / Google Patents / CNIPA | 经 **已有入库** 后成为 `source_kind=patent` 全文文档 → 可抽 |
| OpenAlex + Unpaywall | 仅当 OA PDF **已入库并解析** → `literature`/`oa` 全文文档 → 可抽；摘要命中不可抽 |
| arXiv / Semantic Scholar / scholarly | 同上；无 PDF 入库则不可抽 |
| SureChEMBL | 发现 + 化学图谱；真实配比依赖同族专利全文入库 |
| 互联网快照 | 仅当已作为 SourceDocument 入库且有全文 |

**不在本计划新增检索通道。**

---

## 9. 红线与安全

1. extract **零**生产池写入；confirm **仅** pending + KG。  
2. `force_pending=True`；`origin` 不得伪装进 `_HIGH_ORIGIN` 自动 upsert。  
3. 禁止对 `eligible=false` 的 id 在服务端偷偷触发下载/OCR（返回 4xx + reason）。  
4. 审计字段：`source_id`、`amount_source`、`extraction_method`、页码/chunk_id。  
5. 多实施例不得默默平均合并。

---

## 10. 测试与验收

### 自动化

- [x] eligibility：未入库 / 过短 / failed / ok 四态
- [x] extract：无表 → placeholder warnings；有 mock 表 → 非均分 wt%
- [x] confirm：`promoted_to_pool is False`；pending 调用带 `force_pending`
- [x] API：对 abstract-only / not_ingested id → 400/404
- [x] vitest：知识库行按钮显隐；Modal 展示 `amount_source`

### 手工 / 冒烟

- [x] API 路由挂载 + ineligible 双拒（HTTP）
- [ ] 选一篇已入库、含配方表的专利 PDF → 草稿 wt% 与表一致（依赖现场 KB 样例）
- [x] 未入库 id → 提取失败
- [x] SureChEMBL 无全文占位路径仍可用
- [x] 确认后 `promoted_to_pool=false`

### 验收标准（DoD）

1. [x] 无全文文档无法发起提取（UI + API 双拒）。  
2. [x] 有表专利的草稿 `amount_source=table` 且比重 **不等于** 简单 100/n（除非表本身均分）。  
3. [x] 人审确认后仍 `promoted_to_pool=false`。  
4. [x] 计划文档与实现 checklist 勾选完成。

---

## 11. 工期与优先级（建议）

| 切片 | 估时 | 优先级 |
|------|------|--------|
| A 门禁 + API 骨架 | 2–3 天 | P0 |
| B 表格真实比重 | 3–5 天 | P0 |
| C UX 合流 | 2 天 | P0 |
| D KG 归一 | 2–3 天 | P1 |
| E 散文份数 | 可选 | P2 |

合计主路径约 **1.5–2.5 周**（单人研发轨），视 MinerU/Vision 密钥与样例专利表质量浮动。

---

## 12. 开放问题（实施前可默认）

| 问题 | 默认假设 |
|------|----------|
| `degraded` 解析是否允许提取？ | **允许**，但 Modal 强制 warning |
| 阈值 `raw_text_chars`？ | 500；settings 可配 |
| 是否允许 extract 时同步补跑表格 VLM？ | **允许只读已有页图/表**；若无缓存表信号，可对「已入库附件」异步补抽，但 **不**对未入库 URL 下载 |
| 多实施例默认展示？ | 置信度最高 / 成分最多的一条进 `formulation`，其余在 Modal Tab |

---

## 13. 文件落点（预期）

| 层 | 路径 |
|----|------|
| 资格 / 抽取 | `backend/app/services/embodiment_drafts.py`（新） |
| 表→配比 | 复用 `vision_extract` / `kg/structural_extractor`；新增 normalize helpers |
| API | `backend/app/api/formulations.py` 或 `embodiment.py` |
| Confirm 红线 | 抽共用 `confirm_embodiment_draft()`；SureChEMBL 转调 |
| UI | `EmbodimentDraftModal.tsx`；`SourcesPanel` 知识库行 + eligibility |
| 计划 | 本文 `docs/plans/2026-09-09-embodiment-fulltext-drafts.md` |

---

## 14. 状态

- [x] 产品确认：仅已入库且有全文解析的专利/文献/OA PDF  
- [x] 产品确认：不做仅摘要、无全文的抽配方  
- [x] Slice A–D 实施（门禁 / 表抽 wt% / UX / KG 归一）  
- [x] 验收勾选（pytest + vitest；散文抽取仍延期）  

**已实现**（2026-09-09）：通用 `/api/formulations/*embodiment*` + KB 行按钮 + `EmbodimentDraftModal`。
