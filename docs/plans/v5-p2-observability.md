# v5 P2 全链路可观测 — 实施计划

状态：已确认

## 缺口
- `task_progress.py:36 TaskProgressEvent` 无 `elapsed_ms`/`llm_tokens`，前端卡片仅显示 `stage`
- `GET /tasks/{id}` 已有 `elapsed_ms`（tasks.py:104），但 SSE 事件无，轮询与 SSE 口径不一致

## 方案（最小可观测）
1. 后端 `TaskProgressEvent` 增加 `elapsed_ms`（从 `started_at` 计算），`publish_progress` 每次写入时计算
2. 前端 `api.ts progressToTaskStatus` 透传 `stage/elapsed_ms`，`LoopModal` 与任务条展示 `stage · 耗时`
3. `llm_tokens` 作为 P2 后续（需 llm.py 返回 usage），本迭代先以 `elapsed_ms` 闭环，不新增存储

## 改动
- `backend/app/worker/task_progress.py`（模型 + 计算）
- `frontend/src/api.ts`（透传）
- `frontend/src/components/LoopModal.tsx`（展示，可选）

## 验证
- tsc PASS，SSE 事件含 `elapsed_ms`，`GET /tasks/{id}` 与 SSE 一致
