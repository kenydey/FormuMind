# Wiki P5.1 — Report 导出 + Slide Deck + 金样 E2E

状态：**已落地（2026-09-14）**  
上游：[`2026-09-14-wiki-p5-report.md`](./2026-09-14-wiki-p5-report.md)

## 交付

| 项 | 说明 |
|----|------|
| Slide Deck | 模板 `deck`：`---` 分页 Markdown + PPTX 导出 |
| 导出 API | `POST /api/wiki/dossier/report/export` · `format=md\|docx\|pdf\|pptx` |
| 能力探测 | `GET /api/wiki/reports/templates` → `export` caps |
| Hub | 生成 + 导出 MD/DOCX/PDF/PPTX 按钮 |
| 软依赖 | `pip install -e '.[report_export]'`（python-docx / fpdf2 / python-pptx） |
| 金样 E2E | `tests/test_wiki_p5_export_e2e.py`：要求→文献→DOE→台账→闭环→卷宗→报告→导出 |

## 约束

- disclaimer 仍为 `draft_not_claims`
- PDF 优先文泉驿微米黑等含拉丁字形的 CJK 字体；缺字体时 ASCII 降级
- 未安装导出库时对应 format → HTTP 501

## 测试

```bash
cd backend && python -m pytest -q tests/test_wiki_p5_report.py tests/test_wiki_p5_export_e2e.py
cd frontend && npm test -- --run src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
```
