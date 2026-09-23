# KG measured 可观测性收口（G5 / G6）

> 状态：**已实现**（2026-09-23）  
> 前置：材料级回流 #106 · 指标感知排序 #107 · 榜单徽标 #108 · 主航道门禁 #135 · page-graph polish #138  
> 约束：旗标门控；不污染 Claims/DOE；不改 sync 短报告 / STORM；不双写 Neo4j

## 0. 缺口与收口

| 已有（main） | 仍缺（本 PR） |
|--------------|---------------|
| sync 透传 `kg_written`；`LabWorkbench` 拼接「KG 回流 N 条」 | tip 无 `data-testid`；`if (res.kg_written)` 对非数字不严谨 |
| `GET /api/kg/feedback/stats`；`KgRelationPanel` 内嵌统计 | 统计条依赖实体搜索命中，Hub 画布空选时不可见 |
| 展开区 `MeasuredMetricHitsBanner` | **折叠榜卡**看不到实测信号（G6「榜卡可见」偏弱） |
| 琥珀色「实测」徽章 | 缺 `measured:campaign_N` →「台账#N」溯源条 |

## 1. 本 PR 做 / 不做

| 做 | 不做 |
|----|------|
| `formatKgWrittenHint` + workbench `data-testid=workbench-save-hint` | 改 `kg_feedback` 写入 / 推荐打分公式 |
| `KgFeedbackStatsStrip`：Workbench 常驻 + Hub 材料图画布 compact | 新 Hub 页签 / Neo4j |
| 榜卡头 `card-measured-chip`（折叠可见） | 运营真人 G1–G6 手测（仍归运营勾） |
| 关系行「台账#N」provenance 芯片 | Owner Phase 2 |

## 2. 文件

| 文件 | 改动 |
|------|------|
| `frontend/src/components/kgMeasuredObservability.ts` | tip + chip 纯函数 |
| `frontend/src/components/KgFeedbackStatsStrip.tsx` | 常驻统计条 |
| `frontend/src/components/LabWorkbench.tsx` | tip + strip + sync 后 refreshKey |
| `frontend/src/components/FormulaLeaderboard.tsx` | 折叠实测芯片 |
| `frontend/src/components/knowledge-hub/HubGraphPane.tsx` | canvas 顶 strip |
| `frontend/src/components/KgRelationPanel.tsx` | 台账# campaign 芯片 |
| `docs/plans/2026-09-22-grayscale-kg-maintrack.md` | 标注 G5/G6 代码侧就绪 |
| `backend/tests/test_grayscale_kg_maintrack_gate.py` | sync→kg_written→stats 材料级锁 |

## 3. 验证

```bash
cd frontend && npx vitest run src/components/kgMeasuredObservability.test.tsx \
  src/components/KgFeedbackStatsStrip.test.tsx src/components/measuredMetricHits.test.tsx \
  src/components/cardMeasuredChip.test.tsx
cd backend && python -m pytest -q tests/test_grayscale_kg_maintrack_gate.py \
  tests/test_kg_provenance.py
# Hub 活证（需 Vite→API）：
cd frontend && node scripts/kg_measured_obs_ui_proof.mjs
```

## 4. 环境说明

- Hub · 图谱统计条已在无 ELN 环境下活证（`/api/kg/feedback/stats` 只读 SQLite KG）。
- Workbench 台账创建仍依赖 Datalab ELN（`FORMUMIND_DATALAB_REQUIRED`）；本云 VM 无 Docker 时无法起 `:5001`，台账 strip / sync tip 以同源组件单测 + `test_sync_kg_written_*` 覆盖。
- 运营真人 G1–G6 仍归 `2026-09-22-grayscale-kg-maintrack.md` 勾选。
