# Next Top-5″：Datalab 软降级 · auto_patch 产品化 · 金样闭环运维 · 长任务取消

> 状态：**已实现**（2026-09-25）  
> 前置：`main @ ce3335f`（Top-5′ G1–G4 / STORM 默认开 / auto_patch 白名单）  
> 分支：`cursor/next-top5-datalab-autopatch-loop-tasks`  
> 批项：**#2–#5**（#1 Owner Phase 2 本轮不做）

## 0. 本批做 / 不做

| # | 做 | 不做 |
|---|----|------|
| 2 | `datalab_required` **默认 False**；`campaign_backend` / `experiment_backend` **默认 `auto`**；不可达 → sqlite 本地台账；health/UI「本地台账」提示 | 改 compose ELN 栈硬依赖；静默冒充 ELN |
| 3 | auto_patch **仍默认关**；Hub/EnvFlag 产品化文案（白名单事件）+ Settings CTA；smoke 可选开闸验证 | 默认开 auto_patch；LLM 洗表格 |
| 4 | 金样闭环 in-process gate + 活栈 smoke 运维说明；Workbench 项目级「建议开 auto_loop」引导 | 全局默认开 `auto_loop_on_sync` / `auto_adopt` |
| 5 | STORM / KG 关系 rebuild：`awaitTaskStream` + 取消按钮 + 完成后自动刷新 | 默认并行 STORM；Neo4j |

## 1. 约束

- Claims / DOE 隔离不变；不扩 Neo4j
- Docker ELN 栈仍 `DATALAB_REQUIRED=true` + `backend=datalab`
- auto_patch / auto_loop / auto_adopt 产品默认仍关

## 2. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_top5_rollout_defaults.py \
  tests/test_datalab_soft_degrade.py \
  tests/test_experiment_backend_auto.py \
  tests/test_datalab_eln.py \
  tests/test_wiki_p4_dossier.py \
  tests/test_golden_rd_loop_gate.py \
  tests/test_grayscale_kg_maintrack_gate.py
cd frontend && npx vitest run \
  src/components/InfraHealthBanner.test.tsx \
  src/hooks/useTaskCancel.test.ts \
  src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
# 活栈可选：
# python3 scripts/golden_rd_loop_smoke.py
# FM_ENABLE_AUTO_PATCH=1 python3 scripts/golden_rd_loop_smoke.py
```
