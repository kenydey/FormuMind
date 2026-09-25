# Next Top-5 B→E：闭环金样 · 推荐解释 · KB 面板 · 主路径向导

> 状态：**已实现**（Batch B→E；跳过 A 供应）  
> 基线：`main@19a1802`  
> 分支：`cursor/next-bcde-loop-explain-kb-wizard`  
> 用户决策：**跳过 A 供应主数据**（数据源不足）  
> 约束：不扩 Neo4j；不默认 auto TTL / 关 dual-write / 全局 auto_loop / auto_adopt / soft-correct；Claims/DOE 边界不破

## 落地清单

| 批 | 交付 |
|----|------|
| **B** | `should_trigger_loop_after_sync` 支持项目 workspace OR；`campaign_loop_status`；sync/GET 返回 `loop_status`；Workbench 状态条；`tests/test_project_auto_loop.py`；`scripts/golden_doe_loop_e2e.py` |
| **C** | `FormulationExplain` + `formulation_explain.py`；workflow 挂载；FormulaExplainPanel |
| **D** | `GET /api/kb/quality-ops` + Hub「质量运营」页（含探针） |
| **E** | ActionsPanel `PathWizard`；`EnvFlag.maturity` + Settings 徽章 |

## 验收

```bash
cd backend && .venv/bin/python -m pytest -q \
  tests/test_workbench_loop.py \
  tests/test_project_auto_loop.py \
  tests/test_formulation_explain.py \
  tests/test_kb_quality_ops.py \
  tests/test_env_flag_maturity.py
cd frontend && npx vitest run \
  src/components/FormulaLeaderboard.explain.test.tsx \
  src/components/PathWizard.test.tsx \
  src/components/knowledge-hub/HubQualityPane.test.tsx
```
