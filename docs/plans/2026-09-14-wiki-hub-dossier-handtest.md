# Hub 手测清单 — Project Dossier / Report / Claims·DOE 边界

状态：**P4.6 质量门配套（2026-09-14）**  
蓝图：[`2026-09-14-wiki-project-dossier.md`](./2026-09-14-wiki-project-dossier.md) §P4.6  
自动化：`scripts/hub_dossier_handtest_smoke.py` · 单测 `tests/test_wiki_dossier_claims_doe_regression.py`

---

## 0. 前置旗标

在运行中的 API 环境开启（默认多为关，灰度再开）：

| 环境变量 | 建议手测值 |
|----------|------------|
| `FORMUMIND_WIKI_ENABLED` | `true` |
| `FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED` | `true` |
| `FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED` | `true` |
| `FORMUMIND_WIKI_DOE_CONSTRAINTS` | `true`（验 soft hint **不**读 L2） |
| `FORMUMIND_WIKI_CHAT_BLEND` | `true`（验 Claims 过滤） |
| `FORMUMIND_WIKI_DOSSIER_AUTO_PATCH` | `false`（先手动 refresh） |
| `FORMUMIND_WIKI_DOSSIER_LLM_NARRATIVE` | `false`（可选另开一轮） |

API 冒烟（创建项目 → ensure → refresh → pack → report）：

```bash
python3 scripts/hub_dossier_handtest_smoke.py
# 或
FM_BASE=http://127.0.0.1:5173 python3 scripts/hub_dossier_handtest_smoke.py
```

Hub UI Playwright（需本地前端 + 活动项目；旗标关时 soft-warn）：

```bash
node frontend/scripts/hub_dossier_smoke.mjs
```

组件单测：

```bash
cd frontend && npm test -- --run \
  src/components/knowledge-hub/HubWikiPane.test.tsx \
  src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
```

---

## 1. Hub Wiki · 项目卷宗

| # | 步骤 | 期望 |
|---|------|------|
| W1 | 新建/打开项目，设活动 `project_id` | 顶栏/工作区有活动项目 |
| W2 | Knowledge Hub → **Wiki** → 点「**项目卷宗**」 | 打开 `themes/project-{id}.md`；`data-testid=hub-wiki-open-dossier` |
| W3 | Reader 侧栏 / meta | 可见 `section_revisions`、`unreviewed` / LLM Flag（`hub-wiki-dossier-meta`） |
| W4 | 点「**刷新卷宗**」 | 成功；S1 表含要求指标（盐雾/VOC 等） |
| W5 | 列表可搜到 `themes/project-*` | Chat blend 可命中（非 Claims） |

---

## 2. Hub Reports · 基于卷宗生成

| # | 步骤 | 期望 |
|---|------|------|
| R1 | Hub → **Reports** | 文案标明依赖 DossierPack / `wiki_dossier_report_enabled` |
| R2 | 选 **briefing** → 生成 | 写入 `reports/project-*-briefing.md`；预览含溯源/`source_ids` |
| R3 | 检查 disclaimer | 明确 **`draft_not_claims`** / 不得作 Claims |
| R4 | 可选：feasibility / deck；导出 md | 导出成功或软依赖缺失时友好提示 |

---

## 3. Claims / DOE 信任边界（必测）

| # | 步骤 | 期望 |
|---|------|------|
| C1 | Chat 问与卷宗相关的问题 | 回答可引用 Wiki 编译结论；**Claims/引用列表无 `source=wiki`** |
| C2 | 人为在 dossier/report front-matter 写入假 `bounds_json`（仅测试库） | `wiki_parameter_bounds` / 因子建议 **不出现** 该名；路径不含 `themes/project-` / `reports/` |
| C3 | DOE soft hint / factor suggest | 仅 L1 `systems/`（及 pitfall 禁区）可出现；**不硬采纳** S6 叙述建议 |
| C4 | 关 `wiki_project_dossier_enabled` | ensure/卷宗按钮失败或隐藏；现网其它路径无回归 |

单测锁定（CI）：

```bash
cd backend && python -m pytest -q tests/test_wiki_dossier_claims_doe_regression.py
```

---

## 4. 金样路径（可选，与 P5.1 E2E 对齐）

要求 → 检索入库 → DOE → 台账 → 闭环 → **刷新卷宗** → 五表非空 → 生成 briefing → 导出。

参见：`backend/tests/test_wiki_p5_export_e2e.py`。

---

## 5. 通过标准

- [ ] API smoke 脚本全绿（或明确标出未开旗标）
- [ ] Hub W1–W5、R1–R3 手测通过（Reports 页徽标为「卷宗」而非「预留」）
- [ ] C1–C3 边界无破窗
- [x] `test_wiki_dossier_claims_doe_regression.py` 全绿（CI / 本地 pytest）
- [x] `test_wiki_optimize_dossier_hook.py`：optimize 完成通知 `optimize_completed`
