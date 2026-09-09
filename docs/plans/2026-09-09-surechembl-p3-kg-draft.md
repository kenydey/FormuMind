# SureChEMBL P3 — KG / 半自动专利配方（人审）

状态：**已实现**（2026-09-09）  
前置：P0 lookup · P1 substitutes · P2 content search

## 目标

1. **KG 优先**
   - 实体：`chem:surechembl:{chemical_id}`、`patent:scpn:{doc_id}`
   - 边：`appears_in`（默认）；`claimed_in`（仅当能区分 claims section）
   - 属性：assignee / pub_date / frequency / similarity（边 metadata + 实体字段）

2. **配方抽取（谨慎）**
   - 仅用户对选中专利触发「提取实施例草稿」
   - 输出 Formulation 草稿 + `origin=surechembl` + `needs_review=true`
   - 确认后：KG 写入 + 原料 `force_pending`；**禁止**写入生产配方池 / 静默 upsert 原料

## 非目标

- 不自动批量从检索结果灌图谱
- 不把草稿推进 leaderboard / RAW_MATERIALS
- 不声称实施例 wt% 准确（无量表时用占位并警告）

## 实现要点

| 层 | 位置 |
|----|------|
| KG ingest | `backend/app/services/surechembl_kg.py` |
| Draft extract/confirm | `backend/app/services/surechembl_drafts.py` |
| API | `POST /api/surechembl/kg/ingest-document` · `extract-example-draft` · `confirm-example-draft` |
| UI | SourcesPanel surechembl 行：入库图谱 / 提取实施例草稿；`SurechemblDraftModal` 人审 |

## 验收

- [x] 文档入库图谱后可查到 patent/chem 实体与 appears_in
- [x] 提取草稿不落库；确认后原料进 pending；`promoted_to_pool=false`
- [x] UI 对 surechembl Evidence 提供操作入口
- [x] pytest + vitest + 实网冒烟

## 后续

真实比重 / 多源全文草稿（**仅已入库且有全文解析**）见：  
[`2026-09-09-embodiment-fulltext-drafts.md`](./2026-09-09-embodiment-fulltext-drafts.md)（P3.1，已实现）。

检索命中 **一键入库全文** 以挂接 P3.1 见：  
[`2026-09-09-kb-ingest-evidence-fulltext.md`](./2026-09-09-kb-ingest-evidence-fulltext.md)（P3.2，待实施）。
