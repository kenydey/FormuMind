# Wiki Project Dossier — P4.0–P4.5 实现记录

状态：**P4.0–P4.6 + P5/P5.1 已落地；本文件补 P4.6 质量门（2026-09-14）**  
蓝图：[`2026-09-14-wiki-project-dossier.md`](./2026-09-14-wiki-project-dossier.md)

---

## 已交付

| 切片 | 说明 |
|------|------|
| P4.0–P4.2 | ensure / patch / refresh · pack 填实 · 事件钩子（auto 默认关） |
| P4.3 | `dossier_narrative.py`：节级 LLM 叙述；校验禁表/禁伪图；失败保留旧叙述；`use_llm` API |
| P4.4 | Hub Wiki「项目卷宗 / 刷新卷宗」；Reader 展示 `section_revisions` / Flag；Chat 对 `themes/project-*` 轻微加权 |
| P4.5 | Reports 占位改为「基于卷宗生成」并标明各模板主读节（P5 已接 Generate） |
| P4.6 | Claims/DOE 回归单测；DOE 跳过 `theme`/`report`/`themes/project-*`/`reports/`；Hub 手测清单 + smoke |

## 旗标

- `wiki_project_dossier_enabled`（默认 false）
- `wiki_dossier_llm_narrative`（默认 false）
- `wiki_dossier_auto_patch`（默认 false）
- `wiki_dossier_report_enabled`（默认 false）
- `wiki_dossier_vertical_addendum`

## 测试

```bash
cd backend && python -m pytest -q \
  tests/test_wiki_p4_dossier.py \
  tests/test_wiki_dossier_claims_doe_regression.py
cd frontend && npm test -- --run src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
python scripts/hub_dossier_handtest_smoke.py   # 需本地栈 + 旗标
```

手测清单：[`2026-09-14-wiki-hub-dossier-handtest.md`](./2026-09-14-wiki-hub-dossier-handtest.md)

## 未做 / 非本切片

- Hub 外的独立卷宗编辑器
- Claims/DOE 硬边界解锁（仍见 Compiled Memory 蓝图 §5）
