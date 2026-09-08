# SureChEMBL P2 — 资料检索：专利化学内容通道

状态：**实施中**（2026-09-08）  
前置：P0 lookup · P1 substitutes structure+patents

## 目标

研究/文献检索增加独立 source：`surechembl`（与 EPO/Google `patents` **并列、互不替换**）。

| 能力 | 实现 |
|------|------|
| Keyword content | `POST /search/content?query=&page=&itemsPerPage=` |
| Evidence | `source=surechembl`，`identifier=docId`（SCPN） |
| 可点开 | `url` → Google Patents；`url_alt` → SureChEMBL document |
| 可选化学标注 | `document_chemistry(doc_id)` 客户端可用；热路径 stream **默认关闭**（避免拖垮共享 source executor），P3/人审可再挂 |


## 非目标

- 不替换 `search_patents` / EPO / Google
- 不自动 bulk upsert 标注化合物（P3 KG/人审）
- molbloom 徽章属旁路能力，本切片不做

## 验收

- [x] SourceTypePicker 含「专利化学 SureChEMBL」
- [x] `source_types` 含 `surechembl` 时 Evidence 出现 `source=surechembl`
- [x] 文档行可点 Patents / SureChEMBL 链接
- [x] 关闭 `FORMUMIND_SURECHEMBL` 时该源降级为空且不拖垮其它源
- [x] pytest + vitest + 实网冒烟

状态：**已实现**（2026-09-08）
