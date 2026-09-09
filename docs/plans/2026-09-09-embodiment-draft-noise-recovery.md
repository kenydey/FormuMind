# P3.1b — 实施例草稿噪音治理与真实配比表恢复

状态：**计划待实施**（2026-09-09）  
触发：CN1227312C 全文提取 → `amount_source=placeholder`，原料为英文句段噪音（如 `Typical compositions may include` / `Translated from`），均分 ~16.67 wt%  
前置：P3.1 [`2026-09-09-embodiment-fulltext-drafts.md`](./2026-09-09-embodiment-fulltext-drafts.md) · P3.2 [`2026-09-09-kb-ingest-evidence-fulltext.md`](./2026-09-09-kb-ingest-evidence-fulltext.md)

## 1. 决策摘要

| 做 | 不做 |
|----|------|
| **立刻停掉** Title-Case 句段当原料的占位启发式 | 把噪音草稿继续当「可用配方」给人审 |
| 入库时 **保留 HTML→GFM 表格**，让 P3.1 表路径真正跑通 | 先上 Slice E 散文 LLM 当第一刀 |
| HTML 无表时走 **PDF / hybrid** 表抽取（用户入库路径内） | 提取时对未入库 URL 偷偷下载 |
| 对已入库「被剥扁」文本做 **高精度行恢复**（可选） | 无证据 span 的幻觉配比 |
| 有页面图/扫描表时再接线 **VisionFormulation** | 写生产配方池 / 静默 upsert 原料 |

一句话：**先止损噪音，再恢复表格结构；散文 LLM 排在表路径之后。**

---

## 2. 现象（用户截图）

- 标题：硅烷基涂料组合物… · `CN1227312C`
- `origin=patent_fulltext` · `amount_source=placeholder` · `needs_review=true`
- 原料列：`Silane` / `Typical compositions may include` / `The coating composition may` / `Coatings for food and` / `The primer composition includes` / `Translated from`
- 全部 `additive` · 均分 **16.6667 wt%**
- 文案已诚实提示「未识别到可用配方表」——但 **仍展示假原料行**，人审负担过大、可信度被摧毁

---

## 3. 根因（代码级）

```text
Google Patents HTML (/en)
  → patent_text_from_html → _strip_tags（表格结构被剥成散文行）
  → SourceDocument + chunks（eligible 仍通过：字数够）
  → extract_embodiment_draft
       parse_markdown_tables / parse_html_tables → []
       fallback: Title-Case 正则扫前 8000 字 → 取前 6 个「名字」→ 100/n
```

| # | 根因 | 落点 |
|---|------|------|
| R1 | 无表时用 `\b([A-Z][a-z]+…)\b` 扫英文，Google Patents 机翻/套话命中率极高 | `embodiment_drafts.py` placeholder fallback |
| R2 | `patent_prefer_html=True` 默认走 HTML；`_strip_tags` 丢弃 `<table>`，P3.1 **永远看不到** GFM/`<table>` | `pdf_downloader.py` |
| R3 | 许多 CN 专利真实配比在 **PDF 页表 / 附图**，HTML 正文只有描述句 | 入库策略 |
| R4 | 仓库已有 `VisionFormulation` / hybrid / vision table，**未接入** `extract_embodiment_draft` | 接线缺口 |
| R5 | Slice E（散文重量份 LLM）**未实现**；且即使实现也治不好「表被剥掉」 | 优先级误区 |

**结论：** 这不是「人审不够」，而是 **入库丢结构 + 抽取用错误启发式补洞**。继续点 confirm 只会把噪音推进 pending/KG。

---

## 4. 目标

1. **止损**：无可用表时，草稿 **不得** 用句段碎片冒充原料；UI 明确「无可抽取配方组分」。
2. **表路径复活**：HTML 中真实存在的 Component/wt% 表 → 入库后可 `amount_source=table` 且非均分。
3. **PDF 补强**：HTML 无表信号时，在 **入库阶段** 尝试 PDF/hybrid，写入含表 Markdown。
4. **存量补救**：已入库剥扁文本，能安全恢复「名称 + 数字」行则恢复；否则保持空/化学列表，不编造。
5. **视觉表（后续）**：仅对已缓存页图/扫描页接线 Vision，不在提取热路径乱爬。

### 非目标

- 生产池 / leaderboard 写入  
- 摘要-only Evidence 抽配方  
- 以 Slice E 作为本事故的第一修复  
- 新检索源 Tab  

---

## 5. 实施切片（ROI 序）

### Slice F0 — 杀掉噪音占位原料（P0，~0.5 天）

- [ ] 删除 / 禁用 Title-Case 句段启发式（`findall` 那段）
- [ ] 无表时：`ingredients=[]`，或若有 SureChEMBL chemistry 映射则用 **真实化学名**（仍 `amount_source=placeholder`，均分可保留但须标注来自 SCHEMBL）
- [ ] Modal：空表 + 强提示「未识别到配方表，请换 PDF 入库或人工填写」；可选：空原料时禁用「确认入库」
- [ ] 单测：复现 CN1227312C 式英文套话正文 → **不得**出现 `Typical compositions` / `Translated from`

**验收：** 同款正文再提取，截图级噪音消失；警告仍诚实。

### Slice F1 — 入库保留 HTML 表 → GFM（P0，~1 天）

- [ ] `patent_text_from_html` / section 抽取：在 `_strip_tags` **之前**把 `<table>` 转为 GFM pipe table
- [ ] 单测：带 Component/wt% 的 HTML fixture → `parse_markdown_tables` 非空 → extract `amount_source=table`、wt% 非均分
- [ ] 文档：已入库旧文档需 **再入库全文**（或提供轻量 reparse）才吃到 F1

**验收：** 新入库的「HTML 含表」专利，一点提取即可出真实比重。

### Slice F2 — HTML 无表 → PDF/hybrid 表路径（P0/P1，~1–2 天）

- [ ] 检测 HTML 正文无 table 信号且存在 `citation_pdf_url`（或 GP PDF）时，入库走现有 `parse_document` / hybrid
- [ ] 仍挂在用户「入库全文」/ auto ingest，**不**在 extract API 里偷下未入库 URL（守 P3.1 红线）
- [ ] 失败 reason 可读：`html_no_table` / `pdf_timeout` / `pdf_no_table`

**验收：** HTML 无表、PDF 有表的样例 → 入库后 extract 为 `table`。

### Slice F3 — 剥扁文本行恢复（P1，~1 天，可与 F2 并行）

- [ ] 在实施例标题附近解析 `原料名 + 数字 (+ wt%/重量份/份)` 连续 ≥2 行
- [ ] 高精拒绝句段行；`amount_source` 标 `table` 或 `prose`（勿伪称扫描 OCR）
- [ ] 单测：strip 后的 `Epoxy resin 40` 行可恢复；套话行不可恢复

**验收：** 不重新下载也能救回一部分存量 KB。

### Slice F4 — VisionFormulation 接线（P1，~2–3 天）

- [ ] 仅当有 **已存储** 页图 / hybrid 标了 table-as-image 时调用
- [ ] `VisionIngredient.concentration` → 现有 `_amounts_to_weight_pct`
- [ ] 功能开关；人审强制

**验收：** 表在图里的 fixture → 非空真实配比草稿。

### Slice E — 散文重量份 LLM（P2，暂缓）

- 仍按原 P3.1：默认关、要 evidence_span、不得标 `table`
- **本事故不优先**：治不好 R1/R2；且英文 GP 正文上更容易幻觉份数

---

## 6. 推荐实施顺序

```text
F0（止损） → F1（HTML 表复活） → F2 ∥ F3 → F4 → E（可选）
```

合计约 **3–5 天** 可完成 F0–F2（核心价值）；F3/F4 按表命中率指标再加码。

---

## 7. 指标（决定是否加 F4/E）

| 指标 | 用途 |
|------|------|
| extract 次数中 `amount_source=table` 占比 | 表路径是否真正工作 |
| `ingredients=[]` 的 placeholder 占比 | 止损是否生效（应 ↑，噪音草稿 ↓） |
| 入库 `html_no_table` → PDF 成功补表率 | F2 价值 |
| confirm 次数 / 人审驳回（若有） | 草稿是否开始可用 |

**决策：** table 占比仍低且多页图 → 投 F4；大量中文「A 30重量份」散文且无表 → 再开 E。

---

## 8. 红线

1. 无表不得用句段冒充原料；placeholder 不得标成 `table`。  
2. extract 不写生产池；confirm 仍只 KG + `force_pending`。  
3. 不在 extract 热路径对未入库文档现场长下载/OCR（引导先「入库全文」）。  
4. 多实施例不静默合并。  

---

## 9. 文件落点（预期）

| 层 | 路径 |
|----|------|
| 止损 + 行恢复 + extract | `backend/app/services/embodiment_drafts.py` |
| HTML 表保留 / PDF 回退 | `backend/app/services/pdf_downloader.py` · `fulltext_fetcher.py` · `kb_ingest.py` |
| Vision 接线 | `embodiment_drafts.py` · `vision_extract.py` |
| UI 空态 | `frontend/src/components/EmbodimentDraftModal.tsx` |
| 测试 | `backend/tests/test_embodiment_drafts.py` · `test_pdf_downloader.py` · 含 CN1227312C 式 fixture |
| 计划 | 本文 |

---

## 10. 状态

- [x] 事故复盘（CN1227312C 截图）  
- [x] 根因定位（strip_tags + Title-Case fallback）  
- [x] 优化方案成文  
- [ ] Slice F0–F1 实施  
- [ ] 同专利回归冒烟  

**下一步实施顺序：** F0 → F1（可同一 PR）；F2 紧随。
