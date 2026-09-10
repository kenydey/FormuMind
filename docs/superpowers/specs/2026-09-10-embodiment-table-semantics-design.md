# 实施例表语义门禁 + 入库/origin 补强（A+B）

> 状态：待用户审阅规格  
> 日期：2026-09-10（落地日可能跨至 09-11）  
> 触发：截图 CN102528001B（公开号当原料）· CN120693379A（半对配方 + 脏名 + 全 additive）  
> 前置：[2026-09-09-embodiment-draft-noise-recovery.md](../../plans/2026-09-09-embodiment-draft-noise-recovery.md)（F0–F3 已落地）

## 1. 背景与根因

F0–F3 已止住「无表时用英文句段冒充原料」。当前能出 `amount_source=table` 的草稿说明**全文已在库**，瓶颈不在「没全文 / 热路径缺 OCR」。

| 失败模式 | 根因 |
|----------|------|
| A：原料列是公开号 | 默认按 `len(ingredients)` 最大选表；引用/现有技术表行数常压过真实配方表；名列 fallback=第 0 列、量列=最后一列 |
| B：半对配方 + 「…preparation method」类脏名 + 全 additive | 选到了有配比的表，但单元格/机翻污染；`role` 写死 `"additive"` |
| origin 误标 `literature_fulltext` | `_origin_for_kind` 对非 patent `source_kind` 默认 literature；与是否真有专利全文无关 |

OCR/MinerU 仅影响**入库** markdown 质量；`extract_embodiment_draft` **禁止**热路径再下载/OCR（保持不变）。

## 2. 目标与非目标

### 目标

1. **表语义门禁**：默认 primary 优先真实配方/实施例表，拒绝公开号表与明显非配比表。
2. **列/行硬拒绝**：公开号名、年份量、错误表头不得 silently 变成原料+wt%。
3. **脏名降置信**：制备方法类污染进 warnings，不静默当真成分。
4. **Role**：接入既有 `formulation_linker._infer_role`，未知为 `unknown`。
5. **origin / source_kind**：专利号可归一时标 `patent_fulltext` / `patent`；web/local 等不再默认 literature。
6. **仅新提取**：不改存量已生成草稿；不提供本轮「按新规则重提」按钮。

### 非目标

- 提取热路径 OCR / 再下载全文  
- F4 VisionFormulation 接线  
- Slice E 散文 LLM  
- 写生产配方池 / 静默 upsert 原料  
- 存量草稿批量重抽或 Modal「重提」UX  
- 默认改云端 parse profile  

## 3. 范围切片

### A — `embodiment_drafts.py` 表语义

**A1 表打分（替换 densest-only）**

对每张通过 `table_to_ingredients` 的候选表计算 `score`：

| 信号 | 分 |
|------|-----|
| 附近/label 含 Example/实施例/配方 | +3 |
| 表头匹配 component/ingredient/原料/组分 且量列匹配 wt%/重量份/phr/% | +4 |
| 行名 ≥50% 为公开号形态 | −10（通常整表淘汰） |
| 表头或前文含 patent/publication/prior art/现有技术/对比实施例对照 | −5 |
| 有效成分行数（过硬拒绝后） | +min(n, 8) * 0.1（弱 tie-break） |

排序：`(-score, -valid_rows, label)`。`score < 0` 的表不进入 `embodiments` 列表（或仅进列表但不作 primary；实现选 **不进入默认候选**，避免 UI 仍默认脏表）。

多表时 warnings 继续提示「检测到 N 个…」，并追加一句：默认按表语义分，而非成分最多。

**A2 硬拒绝（`table_to_ingredients` / `_column_map`）**

- 名称匹配专利公开号正则（至少 `CN|US|EP|DE|WO|JP|KR` + 数字）→ 丢弃该行；有效行 `<2` → 整表 `None`  
- 量值 ∈ [1900, 2100] 且 unit/表头不像配比（无 wt/%/份/phr）→ 丢弃该行或整表  
- 表头命中 Title / Patent / Publication / Document 等 → **禁止**作名列；若无合法名列 → 整表 `None`（禁止 fallback `name_idx=0`）  
- 无合法量列时禁止盲目 `amt_idx=last`，除非该列多数单元格可解析为合理配比数（非年份）

**A3 脏名**

名称匹配 `(?i)preparation\s+method|及其制备|and its prepar` 等 → `confidence` 上限 0.45，warnings 追加「名称可能含标题污染」。

**A4 Role**

`role = formulation_linker._infer_role(name)`；无命中为 `"unknown"`（UI 已能展示字符串即可，本轮不强制改前端枚举）。

### B — origin / 入库标签

**B1 `_origin_for_kind`**

1. 若传入 doc 的 identifier/title/filename 经 `normalize_patent_pub` 可得公开号 → `patent_fulltext`  
2. `source_kind` 含 `patent` → `patent_fulltext`  
3. `oa` / `openalex` → `oa_pdf`  
4. `literature` / `arxiv` / `paper` / `scholar` → `literature_fulltext`  
5. `web` / `local` / `upload` / `pasted` / `image` / `api` → **`document_fulltext`**（新字面；加入 confirm `_ALLOWED_ORIGINS`）  
6. 不再把未知 kind 默认成 `literature_fulltext`

**B2 persist `source_kind`**

在 `fulltext_fetcher._persist_fulltext`（或调用处）：若 Evidence identifier 可归一为专利公开号，则 `source_kind="patent"`，即使上游 `classify` 曾因 URL 形态返回 `web`。

**B3 表信号审计（轻量）**

提取结果 `warnings` 或 draft 可选字段 `text_provenance`：`html_table | pdf_table | markdown_table | prose | none`（能从现有文本启发式判断则填；不确定可省略）。**不**改变 F2 策略默认值。

## 4. 数据流（不变骨架）

```text
SourceDocument.full_text ∪ chunks
  → parse_markdown_tables / parse_html_tables
  → table_to_ingredients（硬拒绝）
  → score_table → sort → primary
  → 无表则 F3 prose → 仍空则 ingredients=[]
  → needs_review=true；confirm 白名单含 document_fulltext
```

## 5. 测试与验收

| ID | 断言 |
|----|------|
| T1 | 合成「公开号 \| 标题 \| 年份」表 → `table_to_ingredients` 为 None 或不进 embodiments |
| T2 | 同文并存引用表 + Component/wt% 实施例表 → primary 为配方表 |
| T3 | 脏名含 preparation method → warning + 低 confidence |
| T4 | role 不再恒为 additive（至少命中 linker 规则的原料） |
| T5 | `_origin_for_kind`：patent 号 → patent_fulltext；web → document_fulltext |
| T6 | persist：identifier=CN… → source_kind patent |
| T7 | 既有 F0–F3 单测仍绿 |

产品验收（手工）：新提取 CN102528001B 类不再默认公开号配方；CN120693379A 类主要组分可抽且有脏名警告。

## 6. 风险与回滚

- 过严拒绝导致「真表」被丢掉 → 放宽到空草稿（已有 UX），优于错表；可用 warnings 观察假阴性  
- `document_fulltext` 需前后端确认白名单同步，否则 confirm 400  
- 仅新提取：用户须对旧错草稿重新点「提取」才会受益（本轮接受）

## 7. 实施顺序（供后续 plan）

1. A2 硬拒绝 + 单测 T1  
2. A1 打分 + T2  
3. A3/A4 + T3/T4  
4. B1/B2 + T5/T6  
5. 文案 warnings 微调；跑全 `test_embodiment_drafts` / 相关 fulltext 测  

预估：**1–2 人日**。
