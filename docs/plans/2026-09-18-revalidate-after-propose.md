# 入库后自动重校验清告警（独立方案 + MVP）

> 状态：**已实现**（2026-09-18；#116 合入）  
> 前置：#115 校验条可操作化已合入；「入库缺失组分」可点但黄框不刷新  
> 缺口：propose 成功后 `formulationValidateWarnings` 仍是旧文案，用户看不到是否修好  
> 约束：复用 `api.validateFormulations`；失败保留旧告警；不改后端协议；不自动改配方

## 0. 结论摘要

| 本 MVP 做 | 不做 |
|-----------|------|
| `syncIngredientsToMaterials` 成功后重跑 validate | 改 `formulation_gate` |
| 用新结果刷新 leaderboard + warnings | validate 失败时清空告警 |
| 内联文案：`重校验 N→M 条` | 自动消除 REACH/SVHC（仍人工） |
| 横幅/底部共用同一 handler | 新 API |

## 1. 决策锁定

1. **仅在 propose 成功后**重校验；propose 失败不碰 warnings  
2. **重校验失败**：保留旧 warnings，文案加「重校验失败」  
3. **成功**：`setState({ leaderboard, formulationValidateWarnings })` + `scheduleAutosave`  
4. 不走 `enrichFormulationsViaValidate` 的 catch→`warnings:[]` 路径，避免误清空  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-18-revalidate-after-propose.md` | 本方案 |
| `FormulaLeaderboard.tsx` | sync 后重校验 |
| `FormulaValidateBanner.test.tsx` | mock validate，断言告警刷新 |

## 3. 验证

- propose 成功 + validate 返回更少告警 → 横幅缩短 / 文案含「重校验」  
- validate 失败 → 旧告警仍在，文案含「重校验失败」  
- 无组分 / propose 失败 → 不调用 validate  
