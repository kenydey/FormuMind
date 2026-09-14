# Wiki Project Dossier — P4.0 / P4.1 实现记录

状态：**契约 + 骨架已落地（2026-09-14）**  
蓝图：[`2026-09-14-wiki-project-dossier.md`](./2026-09-14-wiki-project-dossier.md)  
拍板：`project_id` 主键 · 自动 patch 默认关 · `*.data.json` P4 双写 · `vertical_addendum` 可选

---

## 已交付

| 项 | 说明 |
|----|------|
| 旗标 | `wiki_project_dossier_enabled` / `wiki_dossier_llm_narrative` / `wiki_dossier_auto_patch`（均默认 false）+ `wiki_dossier_vertical_addendum` |
| 路径 | `themes/project-{id}.md` + `themes/project-{id}.data.json` |
| Pack | `dossier_pack.build_project_dossier_pack`（S1 要求行 + 空壳后续节） |
| Ensure | `dossier.ensure_project_dossier` 八节骨架，无 LLM |
| Addendum | `vertical_addendum` 注册表；内置 `silane` / `silane_conversion` |
| API | `POST /api/wiki/dossier/ensure` · `GET /api/wiki/dossier/{id}` · `GET .../pack` |
| ADR | 附录 A 已记 |

## 测试

```bash
cd backend && python -m pytest -q tests/test_wiki_p4_dossier.py
```

## 未做（下一切片）

- 节级 `patch_section` + 事件钩子（P4.2）
- DOE/lab/loop 表填实
- LLM 叙述（P4.3）
- Hub UI 入口（P4.4）
