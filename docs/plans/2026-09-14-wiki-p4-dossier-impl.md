# Wiki Project Dossier — P4.0–P4.5 实现记录

状态：**P4.3–P4.5（Hub + 叙述 + Report 占位）已落地（2026-09-14）**  
蓝图：[`2026-09-14-wiki-project-dossier.md`](./2026-09-14-wiki-project-dossier.md)

---

## 已交付

| 切片 | 说明 |
|------|------|
| P4.0–P4.2 | ensure / patch / refresh · pack 填实 · 事件钩子（auto 默认关） |
| P4.3 | `dossier_narrative.py`：节级 LLM 叙述；校验禁表/禁伪图；失败保留旧叙述；`use_llm` API |
| P4.4 | Hub Wiki「项目卷宗 / 刷新卷宗」；Reader 展示 `section_revisions` / Flag；Chat 对 `themes/project-*` 轻微加权 |
| P4.5 | Reports 占位改为「基于卷宗生成」并标明各模板主读节 |

## 旗标

- `wiki_project_dossier_enabled`（默认 false）
- `wiki_dossier_llm_narrative`（默认 false）
- `wiki_dossier_auto_patch`（默认 false）
- `wiki_dossier_vertical_addendum`

## 测试

```bash
cd backend && python -m pytest -q tests/test_wiki_p4_dossier.py
cd frontend && npm test -- --run src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
```

## 未做

- 完整 Report 排版 / Generate API（P5）
- Hub 外的独立卷宗编辑器
- 金样 E2E 五表非空（可后续补）
