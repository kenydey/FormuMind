# KB 质量 W5（= Top-5 #5 / 路线图 W3′）：闭环失败重试 + 可选 auto-adopt

> 状态：**已实现**（2026-09-25）  
> 别名：升级评审 Top-5 第 5 项；周计划记为 W3′；用户口令「W5」  
> 前置：W1–W3（#141）；`dispatch_loop_after_sync` 已接线  
> 约束：**不默认开** `auto_loop_on_sync` / `auto_adopt_next_doe_on_loop`；不改 Claims/DOE 语义；不扩 Wiki/Neo4j；不做 W4+ TTL/冷存储

## 0. 本批做 / 不做

| 做 | 不做 |
|----|------|
| Loop 失败后 UI「重试闭环」（`runLoop` 新一轮，非重放失败 task） | 默认打开 `auto_loop_on_sync` |
| 旗标 `auto_adopt_next_doe_on_loop`（默认 False）+ LoopModal / Settings | 从零重做 auto-dispatch |
| 成功且旗标开 → `adoptDoePlanToWorkbench(next_doe)` | W4+ retention / 停写 `suppliers_json` |

## 1. 行为

| 条件 | 行为 |
|------|------|
| `followLoopTask` 失败（非取消） | `loopRetryAvailable=true`；LoopModal / 台账提示可重试 |
| 点「重试闭环」 | 清错 → `runLoop()` |
| Loop 成功 + `auto_adopt_next_doe_on_loop` / FE 勾选 | 自动 adopt `next_doe` 到台账 |
| 旗标关（默认） | 仅写 `doePlan`，需手动 adopt |

## 2. 文件

| 文件 | 改动 |
|------|------|
| `backend/app/config.py` / `env_flags.py` | 旗标 |
| `frontend/src/store/*` / `workflowSlice.ts` | retry + adopt 接线 |
| `frontend/src/components/LoopModal.tsx` / `LabWorkbench.tsx` | UI |
| `docs/plans/2026-09-25-kb-quality-w5-loop-retry-adopt.md` | 本方案 |
| tests | 旗标默认关 + FE adopt/retry |

## 3. 验证

```bash
cd backend && python -m pytest -q tests/test_env_flags.py -k adopt
cd frontend && npx vitest run src/store/slices/workflowSlice.loopRetry.test.ts
```
