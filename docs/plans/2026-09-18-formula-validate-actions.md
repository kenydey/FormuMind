# 推荐榜配方校验条可操作化（独立方案 + MVP）

> 状态：**已实现**（2026-09-18）  
> 前置：#114 门禁丢弃计数已合入 main；质量线收口  
> 缺口：`FormulaLeaderboard` 黄框「配方校验 / 目录补全提示」只读；「入库缺失组分」沉在底部，与告警脱节  
> 约束：复用现有 `validateFormulations` 警告文案 + `proposeMaterialsMany` / `openModal("materials")`；不做新法规引擎、不自动改配方

## 0. 结论摘要

| 本 MVP 做 | 不做 |
|-----------|------|
| 按警告文案分类：`catalog` / `compliance` / `weight` / `other` | 改后端 `formulation_gate` 协议 |
| 黄框内挂动作：「入库缺失组分」「打开材料库」 | 自动改配方权重 / 清 SVHC |
| 入库结果写回横幅旁（替代仅 `alert`） | 全量 REACH 数据库 |
| 底部按钮与横幅共用同一 handler | 大重构 FormulaCard |

## 1. 决策锁定

1. **前端分类**：`classifyValidateWarnings(warnings)` 用子串匹配现有中英文警告（CAS / SMILES / SVHC / REACH / RoHS / weight / 当量）  
2. **catalog 类** → 主按钮「入库缺失组分」→ `proposeMaterialsMany`  
3. **任意有警告** → 次按钮「打开材料库」→ `setOpenModal("materials")`  
4. **compliance 类** → 提示文案「合规项需人工确认」，不提供「一键清除」  
5. 底部「入库缺失组分」保留，调用同一 `syncIngredientsToMaterials`  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-18-formula-validate-actions.md` | 本方案 |
| `frontend/src/utils/validateWarningActions.ts` | 分类 + 建议动作 |
| `frontend/src/utils/validateWarningActions.test.ts` | 单元测试 |
| `frontend/src/components/FormulaLeaderboard.tsx` | 横幅动作区 |
| `frontend/src/components/FormulaValidateBanner.test.tsx` | 横幅动作点击 |

## 3. 验证

- 含「missing CAS / no CAS」警告时横幅出现「入库缺失组分」  
- 点击打开材料库 → `openModal === "materials"`  
- 点击入库 → 调用 `proposeMaterialsMany` 并展示汇总文案  
- 回归：无警告时不渲染横幅  
