# Wiki 项目 dossier → Report 导出（发现性收口）

> 状态：**已实现**（2026-09-18）  
> 前置：P5 / P5.1 已落地生成与 `POST /api/wiki/dossier/report/export`；#116 已合入  
> 缺口：产物抽屉无卷宗 Report 入口；生成后不能一键进货架；导出能力不直观  
> 约束：**不重写** `report.py` / 导出引擎；不默认开 LLM；不并 llm_wiki；旗标仍由运维控制

## 0. 结论摘要

| 已有（P5） | 本 MVP 补 |
|-----------|----------|
| Hub「文档生成」页 + generate/export API | 产物抽屉 `wiki_report` → `openKnowledgeHub("reports")` |
| MD/DOCX/PDF/PPTX 导出 | 生成后「保存到货架」 |
| `wiki_dossier_report_enabled` | 探测 `export` caps 并展示可用格式 |

## 1. 决策锁定

1. **不重复造报告引擎** — 复用 Hub Reports 页  
2. **有活动项目即出现**产物条目（入口，不要求已生成）  
3. **openArtifact("wiki_report")** → Knowledge Hub · reports 页签  
4. **生成成功** → 可选存货架（`saveTextToProjectShelf`）  
5. **caps**：`listWikiReportTemplates` 的 `export` 字段驱动按钮禁用提示  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-18-wiki-dossier-report-hub.md` | 本方案 |
| `artifacts/projectArtifacts.ts` | `wiki_report` kind |
| `uiSlice.ts` | openArtifact 特判开 Hub reports |
| `App.tsx` / `ArtifactDrawer.tsx` | 传入 `activeProjectId` |
| `HubReportsPlaceholderPane.tsx` | 货架保存 + caps |
| 相关 `*.test.ts(x)` | 覆盖入口与保存 |

## 3. 验证

- 有 `activeProjectId` 时产物抽屉出现「卷宗 Report」  
- 点击打开 Knowledge Hub 且 tab=reports  
- 生成后「保存到货架」调用 shelf API（mock）  
- 回归：既有 artifact kinds 不变  
