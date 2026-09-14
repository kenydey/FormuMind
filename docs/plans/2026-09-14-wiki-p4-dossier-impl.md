# Wiki Project Dossier — P4.0–P4.2 实现记录

状态：**P4.2 节级 patch + 切片填实已落地（2026-09-14）**  
蓝图：[`2026-09-14-wiki-project-dossier.md`](./2026-09-14-wiki-project-dossier.md)  
拍板：`project_id` 主键 · 自动 patch 默认关 · `*.data.json` P4 双写 · `vertical_addendum` 可选

---

## 已交付

| 项 | 说明 |
|----|------|
| 旗标 | `wiki_project_dossier_enabled` / `wiki_dossier_llm_narrative` / `wiki_dossier_auto_patch`（均默认 false）+ `wiki_dossier_vertical_addendum` |
| 路径 | `themes/project-{id}.md` + `themes/project-{id}.data.json` |
| Pack | `dossier_pack.build_project_dossier_pack` — S1–S7 切片（文献/配方/DOE/台账/闭环/资产） |
| Ensure | `dossier.ensure_project_dossier` 八节确定性表，无 LLM |
| Patch | `patch_dossier_sections` / `refresh_dossier`；`section_hashes` 幂等跳过 |
| Auto | `notify_dossier_event` / `notify_dossier_event_for_campaign`；默认 OFF |
| Hooks | project update · ingest · DOE persist · workbench sync · loop dispatch（均 gated） |
| Addendum | `vertical_addendum` 注册表；内置 `silane` / `silane_conversion` |
| API | `POST /ensure` · `POST /patch` · `POST /refresh` · `GET /{id}` · `GET /{id}/pack` |
| ADR | 附录 A 已记 |

## 测试

```bash
cd backend && python -m pytest -q tests/test_wiki_p4_dossier.py
```

覆盖：旗标门闩 · ensure+data.json · DOE/loop hydrate · 节级隔离 patch · hash 幂等 · auto_patch 默认跳过 · API ensure/patch/refresh。

## 未做（下一切片）

- LLM 叙述（P4.3）
- Hub UI 入口 / Reader `section_revisions`（P4.4）
- Report 排版（P5）；pack 导出已可用
