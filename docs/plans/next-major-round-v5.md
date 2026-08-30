# 下一大轮（v5）规划 — 自主闭环智能体化

> 基于 `backend/app` + `frontend/src` 2026-08-28 扫描，承接 v2/v3/v4 已交付基线。共 3 项，按价值/风险排序。

## 0. 基线（已冻结 `426ff56`）

| 域 | 已交付 |
|---|---|
| 隔离 | owner 硬校验（campaign/task/phase）+ 403 |
| 可观测 | `CANCELLED` 终态 + `elapsed_ms/stage` + `prewarm` + `bias-trend` SVG |
| 知识 | KG `measured` 回流 + `x1.15` 加成 + 审计报表 |

## 1. P1 — 自主闭环 v2（收敛自停 + 自动下一批）

**证据**：
- `workbench_loop.py:122` `dispatch_loop_after_sync` 仅在 `autoLoopOnSync` 时触发单轮，`LoopModal` 需手动点"迭代"；`loop_history` 已有 `converged` 标志但无自动停止阈值（`tasks.py:840 round_no = len(history)+1`）。
- `BiasTrendPanel` 已可视 `rmse` 阈值 `50`，但阈值仅前端告警，未回灌至后端决策。

**缺口**：用户需手动判断收敛并点"下一轮"，无法无人值守跑 3-5 轮。

**方案**：
1. 后端：`loop` 任务完成时若 `report.converged==False` 且 `autoLoopOnSync==True`，自动调度下一轮 `run_loop_task`（带 `prior_rmse_history`），并在 `loop_history` 写入 `auto_scheduled:true`。
2. 前端：`LoopModal` 增加"自主模式"开关（`autoLoopOnSync` 已有，补"最多 N 轮"输入），展示剩余轮次与自动停止原因。

**改动**：`worker/tasks.py`（`_persist_loop_history` 后调度）、`api/loop.py`（`auto` 参数）、`store/workflowSlice`（`autoLoopMaxRounds`）、`LoopModal`。

**风险**：中 — 需限轮（默认 5）防无限循环，单轮失败即停。

---

## 2. P1 — 知识驱动替代推理（KG → 推荐闭环）

**证据**：
- `kg_chemical_check.py:36 inhibits` 仅单跳，`substitution.py:171 discover_substitutes` 已有但未进入推荐主链路；`FormulaLeaderboard` 的 `kg_compat` 仅展示，不提供一键替代。
- `MaterialSubstitutionModal` 需手动选位点，未与 `measured` 加成联动。

**缺口**：KG 知道"谁可替谁"，但推荐仍盲选，`measured` 材料未优先用于替代。

**方案**：
1. 后端：`recommend` 流程中，对 `inhibits` 命中的配方自动调用 `discover_substitutes`，将 `measured` 材料优先的替代候选注入 `recommended` 列表（`source=substitution`）。
2. 前端：`FormulaLeaderboard` 对 `infeasible` 配方展示"一键替代"按钮，直接打开 `MaterialSubstitutionModal` 并预选 `measured` 候选。

**改动**：`services/recommend.py` 或 `kg_recommend_score.py`（替代注入）、`FormulaLeaderboard.tsx`（按钮）、`MaterialSubstitutionModal`（`measured` 排序）。

**风险**：低 — 复用现有 `discover_substitutes`，仅排序加成。

---

## 3. P2 — 全链路可观测（Trace + 成本）

**证据**：
- `worker/task_progress.py:140 publish_progress` 仅写 `stage/message`，无 `cost_ms`/`token`；`health` 仅 `broker_ok`，无 `prewarm` 耗时趋势。
- 前端 `RagPrewarmBar` 轮询但无历史，`BiasTrendPanel` 仅 `prediction_bias`。

**方案**：
1. 后端：`task_progress` 增加 `cost_ms` 与 `llm_tokens`（从 `llm.py` 回传），`GET /tasks/{id}` 透传；`GET /health/detailed` 补 `prewarm.elapsed_ms` 趋势。
2. 前端：`LoopModal` 与 `SourcesPanel` 在任务卡片角标展示 `elapsed_ms` 与预估成本。

**改动**：`worker/task_progress.py`、`api/tasks.py`、`services/llm.py`（token 计数）、`LoopModal/SourcesPanel`。

**风险**：低 — 只读透传。

## 2. 执行顺序

1. **P1 自主闭环 v2** — 价值最高，直接形成"实验→数据→优化→新实验"无人值守。
2. **P1 替代推理** — 让 KG 从"告警"变为"解法"。
3. **P2 可观测** — 补成本与耗时，支撑运营。

## 3. 验证标准

- 自主闭环：`autoLoopOnSync=true, maxRounds=3` 时，单次 `sync` 自动产生 3 轮 `loop_history` 且末轮 `converged=true` 时停止。
- 替代推理：`inhibits` 配方在推荐中出现 `source=substitution` 候选，且 `measured` 候选排首位。
- 可观测：`GET /tasks/{id}` 返回 `elapsed_ms` 与 `llm_tokens`，前端卡片展示。

## 4. 不做

- 不新增存储表；复用 `loop_history` 与 `task_progress`。
- 不改 `FORMUMIND_MULTI_USER` 默认值。

*待确认后按"方案→代码→测试"闭环逐项推进。*
