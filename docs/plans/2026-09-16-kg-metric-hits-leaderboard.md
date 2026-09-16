# 推荐榜展示指标感知实测命中（独立方案 + MVP）

> 状态：**已实现**（2026-09-16）  
> 前置：[#107](https://github.com/kenydey/FormuMind/pull/107) 已将 `measured_metric_hits` 写入 `form.kg_compat` 并参与软排序  
> 缺口：`FormulaLeaderboard` 仍只展示二元 `measured_materials`「已获实测证据加成」，会把 **差实测降权** 也画成绿色加成，且看不到目标指标  
> 约束：只读展示；不改打分公式；不新 Hub 页；不双写 Neo4j

## 0. 结论摘要

| 已有 | 证据 |
|------|------|
| 后端 hits | `kg_recommend_score.record_kg_compat` → `measured_metric_hits[{material,metric,quality,value,…}]` |
| 推荐卡 | `FormulaLeaderboard` 展开区已有 INHIBITS / 二元实测徽标 |

| 本 MVP 做 | 不做 |
|-----------|------|
| `api.ts` 补齐 `measured_metric_hits` 类型 | 改 `kg_compat_adjust` 系数 |
| 按 quality 分色展示（好加成 / 存在 / 偏弱降权）+ 材料·指标 | 新路由 / Hub 页签 |
| 有 metric hits 时**优先**展示，不再误用二元「实测验证加成」 | 改 warnings 文案生成逻辑（后端已够） |

## 1. 决策锁定

1. **优先级**：`measured_metric_hits.length > 0` → 指标感知条；否则回退原 `measured_materials` 二元条  
2. **聚合**：同卡多 hit 取 best quality（good > presence > poor），文案列出涉及材料与指标  
3. **样式**：good=emerald；presence=sky/slate；poor=amber（降权，非错误红）  
4. **可测**：抽纯函数 `summarizeMeasuredMetricHits` + 小组件 `data-testid=measured-metric-hits`

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-16-kg-metric-hits-leaderboard.md` | 本方案 |
| `frontend/src/api.ts` | `kg_compat.measured_metric_hits` |
| `frontend/src/components/measuredMetricHits.tsx` | 汇总纯函数 + 徽标组件 |
| `frontend/src/components/FormulaLeaderboard.tsx` | 接入徽标 |
| `frontend/src/components/measuredMetricHits.test.tsx` | 单测 |

## 3. 验证

- vitest：good / poor / presence 文案与 `data-quality`；无 hits 返回 null  
- 有 hits 时 Leaderboard 不渲染旧「已获实测证据加成」二元条（组件级或纯函数级覆盖）  
