# Workbench → 卷宗 S5 台账正式路径

状态：**已落地（2026-09-22）**  
关联：金样闭环 [`2026-09-14-wiki-hub-dossier-handtest.md`](./2026-09-14-wiki-hub-dossier-handtest.md) · S4 草稿 [`2026-09-21-s4-draft-ops-handtest.md`](./2026-09-21-s4-draft-ops-handtest.md)

---

## 0. 问题

活栈 `experiment_backend=datalab` 且 ELN 不可达时，Workbench sync 的训练回灌可能落到空 JSON registry，**不写** `ExperimentRow`。卷宗 `_lab_slice` 只读 SQL → S5 空，只能靠 `workspace.measured` 回退。

## 1. 正式路径

```text
Workbench sync (Completed + measurements)
  → persist_workbench_lab_ledger  （SQL ExperimentRow, source=workbench, 带 project_id）
  → registry.add（训练，失败不阻断台账）
  → notify lab_recorded（auto_patch 开时刷新 S4/S5/S8）
  → 用户/脚本 refresh 卷宗 → pack.lab / S5 表有料
```

优先级（S5 hydrate）：

1. `ExperimentRow` where `project_id=…`（正式）
2. `workspace.workbench_campaign_id` → Completed 行（次级）
3. `workspace.measured`（金样/手测回退）

创建 campaign 时若带 `project_id`，会写入 `workspace.workbench_campaign_id`。

## 2. 自动化

```bash
cd backend && python -m pytest -q tests/test_wiki_workbench_lab_dossier_s5.py

# 活栈（需 campaign 后端可达）
python3 scripts/workbench_dossier_s5_smoke.py
```

## 3. 验收

- [x] sync Completed → SQL `ExperimentRow.project_id` 非空
- [x] pack `empty_lab=false`；`source` 为 `experiment:*` / `workbench:*`（非 `workspace.measured`）
- [x] registry/Datalab 失败时 sync 仍 200，台账仍进卷宗
- [x] 幂等 upsert（同 `wb:{campaign}:{item}` label）
- [ ] 活栈 ELN 可达时跑 `workbench_dossier_s5_smoke.py` 全绿（ELN 宕机则以 pytest 为准）
