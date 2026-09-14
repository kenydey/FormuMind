# Wiki P5 — DossierPack Report 生成

状态：**MVP 已落地（2026-09-14）**  
上游：[`2026-09-14-wiki-project-dossier.md`](./2026-09-14-wiki-project-dossier.md) §8

## 目标

从同一 `DossierPack` 生成 Hub 研发草稿，**禁止**把 L2 叙述当 Claims。

## 交付

| 项 | 说明 |
|----|------|
| 旗标 | `wiki_dossier_report_enabled`（默认 false；依赖 dossier） |
| 服务 | `services/wiki/report.py` — 四模板确定性 Markdown |
| 路径 | `reports/project-{id}-{template}.md`（kind=`report`） |
| API | `GET /api/wiki/reports/templates` · `POST /api/wiki/dossier/report` |
| Hub | Reports 页「基于卷宗生成」+ Reader 预览 |
| LLM | 可选执行摘要；受 `wiki_dossier_llm_narrative` + `use_llm`；失败保留确定性稿 |

## 模板 ↔ 切片

| 模板 | Pack 切片 |
|------|-----------|
| briefing | requirements, loop, literature, flags |
| feasibility | requirements, formula, doe, lab, flags |
| formula-compare | formula, loop, flags |
| patent-memo | literature, flags |

每篇报告含 **source_ids / 测量行溯源** 块，disclaimer=`draft_not_claims`。

## 非目标（本 MVP）

- PDF/Word 排版引擎
- Slide deck
- 把报告写入 Claims / DOE 硬边界

## 测试

```bash
cd backend && python -m pytest -q tests/test_wiki_p5_report.py
cd frontend && npm test -- --run src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
```
