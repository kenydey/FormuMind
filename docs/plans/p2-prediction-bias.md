# P2 预测偏差校准 + 前端验证闭环 — 实施方案

> 约束：延续 P0/P1/P2-owner 已落库（`09eefce`），不新增表，复用 `campaign.loop_history`；预测发生在 `registry.add` 之前（避免用刚学过的新数据自测）

## 1. 背景与缺口

- 台账 `Completed` 行回灌 `ModelRegistry` 时，仅有 `ingested count`，缺乏 **预测 vs 实测** 的逐指标偏差，无法验证 surrogate 是否在收敛。
- DOE 下发的 `predicted` 目标值（`tradeoff_analysis._verification_doe_for`）与实测对比无埋点，报告无法引用偏差。
- 前端同步后仅 `toast` 显示 `training_ingested / message`，无可视化偏差。

## 2. 目标

- 台账 `Completed` 行 **回灌前** 用当前模型预测 → 与 `measured` 对比，产出 `prediction_bias`（每 metric 的 `mean_error / rmse / mae / max_abs / n / bias%`）
- **持久化**：`n>0` 时追加 `campaign.loop_history` 轻量条目 `{type:"prediction_bias", at, bias_by_metric, n_rows}`
- **透传**：`PUT /experiments/workbench/sync` 的 `training_message`/`loop_message` 扩展包含偏差摘要，`WorkbenchSyncResponse` 新增 `prediction_bias` 字段（兼容：前端忽略时不影响）
- **前端**：同步成功后 `LabWorkbench` 顶部 `toast/hint` + 可选“偏差”小面板（复用现有 `saveHint` 区域），不新增路由

## 3. 架构图

```
LabWorkbench (AG Grid) ── Completed rows ─┐
                                         ▼
                PUT /experiments/workbench/sync
                     ├─ Datalab batch_sync ──► rows
                     ├─ workbench_training.ingest_workbench_rows(rows)
                     │     ├─ registry.known_labels() 去重
                     │     ├─ ★ _compute_prediction_bias(to_add) — registry.predict BEFORE add
                     │     │     └─ features.vector(form, process) + registry.predict(domain, metric, vec)
                     │     ├─ registry.add(to_add, retrain=True)
                     │     └─ append_loop_history_sync({type:"prediction_bias", ...}) if n>0
                     │     └─ return {ingested, skipped, prediction_bias}
                     ├─ kg_feedback.ingest_measured_evidence(...) (P0 已有, best-effort)
                     └─ dispatch_loop_after_sync(...)
                                         ▼
                           WorkbenchSyncResponse {updated, rows, training_ingested,
                                                  training_message, prediction_bias, loop_task_id}
                                         ▼
                           LabWorkbench saveHint + 可选 BiasPanel（折叠）
```

`_compute_prediction_bias` 仅依赖已训练模型；`n_samples==0` 或无模型时返回空，不阻断回灌。

## 4. 文件变更清单

| 文件 | 改动 |
|---|---|
| `backend/app/services/workbench_training.py` | 新增 `_compute_prediction_bias(to_add, domain, project_id)`；`ingest_workbench_rows` 在 `registry.add` 前调用，`return` 扩展 `prediction_bias`，并 `append_loop_history_sync` |
| `backend/app/api/experiments.py` | `WorkbenchSyncResponse` 新增 `prediction_bias: dict | None`；`sync_workbench` 合并 `train_result["prediction_bias"]` 到响应与 `training_message` 摘要 |
| `backend/tests/test_prediction_bias.py` | 新增 3 用例：有模型时 bias 可计算 / 无模型时空 / 回灌后 loop_history 写入 |
| `frontend/src/components/LabWorkbench.tsx` | 同步成功后解析 `prediction_bias`，在 `saveHint` 下方渲染偏差摘要（折叠/展开），复用现有状态，不新增请求 |
| `frontend/src/api.ts` | `WorkbenchSyncResponse` 类型扩展 `prediction_bias`（可选） |

**不改动**：`campaign_store`、`training.ModelRegistry`（仅只读 predict）、DB schema、alembic。

## 5. 实施步骤

1. **后端偏差计算**（半日）：`features.vector` 复用 `training._dataset` 路径的 `Requirement + reconstruct.formulation_from_factors`，逐 metric 调用 `registry.predict`，聚合指标
2. **API 透传**（0.5h）：扩展 `WorkbenchSyncResponse`，`training_message` 追加 ` | bias ...` 摘要
3. **前端展示**（半日）：`LabWorkbench.tsx` 读取 `res.prediction_bias`，渲染摘要与可选明细表（`metric | n | mean_error±rmse | mae`）
4. **测试**（0.5h）：`test_prediction_bias.py` 3 用例；复跑 `test_workbench_api` + `test_api` 守回归
5. **端到端走查**：建 DOE → 推送台账 → 填 `Completed`+`measured` → sync → 校验 `loop_history` 出现 `prediction_bias` 且前端可见

## 6. 数据结构

```json
prediction_bias = {
  "n_rows": 3,
  "by_metric": {
    "salt_spray_h": {"n":3,"mean_error":12.4,"rmse":18.1,"mae":15.2,"max_abs":28.0},
    "adhesion_mpa": {"n":2,"mean_error":-0.8,"rmse":1.1,"mae":0.9,"max_abs":1.5}
  }
}
loop_history entry = {"type":"prediction_bias","at":"2026-08-26T...Z","n_rows":3,"by_metric":{...}}
```

## 7. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 无模型时 predict 返回 None → bias 为空 | 高 | 低 | 分支返回空 dict，不写 history，不阻断 add |
| 特征向量与训练时不一致 | 低 | 高 | 复用 `features.vector(form, process)` 与 `training._dataset` 同路径；加单测对比 |
| loop_history 膨胀 | 低 | 低 | 仅 `Completed` 回灌时写，且每 batch 一条，不是每行 |
| 前端忽略新字段 | 低 | 无 | `prediction_bias` 可选，后端兼容 |

## 8. 验证标准

- 后端：`test_prediction_bias.py` 3 passed；`test_workbench_api` 保持通过
- 集成：一次真实 `Completed` sync 后 `GET /experiments/workbench/{id}` 的 `loop_history` 含 `type=prediction_bias`
- 前端：`tsc --noEmit` 0 error；同步后页面可见偏差摘要

## 9. 回滚

- 后端：删除 `_compute_prediction_bias` 与 `append_loop_history_sync` 调用即回退
- 前端：隐藏 BiasPanel，不影响主流程
