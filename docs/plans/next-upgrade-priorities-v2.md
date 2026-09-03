# FormuMind 下一步升级优先级 v2（代码分析驱动）

> 基于 `backend/app` + `frontend/src` 2026-08-28 扫描，每项附文件:行号证据。v1 的 5 项已全部落库（`9735897`），本文是 v2。

## 0. 已交付基线（v1 闭环）

| 项 | 提交 | 验证 |
|---|---|---|
| KG 自进化闭环（`kg_feedback.ingest_measured_evidence`） | `6e35705` | 2026-08-28 端到端 `prediction_bias` + `loop_history` 已验 |
| 死 API 清理 `baybe/recommend` | `eb56548` | — |
| 异常透明度硬化 3 处 | `6028520` | 5 用例 |
| owner 预埋 Phase 1（软校验） | `926edb8`/`09eefce` | 4 用例，alembic 0017 幂等 |
| 预测偏差校准 + 前端琥珀条 | `9735897` | 3 用例，`LabWorkbench` 已展示 |

## 1. v2 优先级建议

### P1 — owner Phase 2 硬校验（从"预埋"到"可用"）

**证据**：
- `api_auth.py:152 get_current_owner` 在 `FORMUMIND_MULTI_USER!=true` 时恒 `default`（`L162-163`），`assert_owner` 在 `current==default` 时恒过（`L179-181`），当前为 Phase 1 软校验，仅 debug 日志。
- 3 张表已有 `owner_id` 列 + 索引（`models.py:51/183/471`，`0017_owner_id.py:28-29`，`database.py:169 _ensure_owner_id_column`），写入路径已埋（`campaign_store.py:133/189/393`，`experiments.py:302`）。
- 调用点 4 处（`experiments.py:294/319/336`，`tasks.py:133`）已接 `assert_owner`，但 Phase 1 下等价无校验。
- 前端无 token/owner 切换 UI，`api.ts` 未透传 `Authorization: Bearer` 的 owner 语义。

**缺口**：单 token 部署已满足内部使用，但对外/多团队共享时无隔离；`task_outbox`/`experiments`/`campaigns` 仍可被任意 token 越权读/写。

**建议**：
1. 后端：以 `FORMUMIND_MULTI_USER=true` 为开关，`FORMUMIND_API_TOKENS_JSON='{"alice":"tok1","bob":"tok2"}'` 解析 `token→owner`（`api_auth.py:165-169` 已预留 `owner:token` 解析，补全映射表逻辑），`assert_owner` 在多用户模式下对资源有 `owner_id` 时强校验 403。
2. 前端：`Settings/登录` 入口展示当前 `owner`（`GET /api/auth/status` 已有，补 `owner` 字段），`api.ts` 的 `token` 存 `localStorage` 已有，补"切换身份"指引。
3. 迁移：旧库 `owner_id IS NULL` 的行视为"公共"（仍放行），新创建行强制写入 `owner_id`（已做），避免历史数据阻断。

**改动清单**：`app/middleware/api_auth.py`（`get_current_owner` 映射表 + `assert_owner` 强校验分支）、`app/api/auth.py`（status 透传 owner）、`frontend/src/api.ts` + `frontend/src/components/**`（状态展示）、`tests/test_owner_phase2.py`（越权 403/放行/公共行）。
**风险**：中 — 鉴权默认关闭（`FORMUMIND_MULTI_USER` 未设时行为不变），仅多用户开启时生效，兼容旧部署。

---

### P1 — KG provenance 可观测（文献 vs 实测）

**证据**：
- `kg_feedback.py:170 extraction_method="measured"` + `evidence_ref {source_id:"measured:campaign_…", sentence:"实测 …"}` 已区分来源；`models.py:431 extraction_method String(16)` + `entity_store.py:197 upsert` 已持久化。
- 但**无 UI 区分**：`LabWorkbench`/`TradeoffAnalysis`/`KG 图谱` 前端未展示 `extraction_method`，推荐排序未对 `measured` 加权/过滤（`graph_query.py:37/55` 仅透传 `extraction_method`，未参与排序）。
- `config.py:99 kg_measured_feedback_enabled` 默认开启，但运营侧无法验证"飞轮是否生效"。

**建议**：
1. 后端：`GET /api/kg/entity/{id}/links` 已有，在 `graph_query` 的排序/过滤中增加 `extraction_method` 可选过滤（`?method=measured|rule|llm`），并在 `GET /api/kg/feedback/stats` 新增轻量统计（`measured_performance` 数量、按 campaign 聚合）。
2. 前端：`KG` 抽屉/详情页对 `measured` 证据用琥珀色徽章 + `实测:campaign_#` 可点击回跳台账；`LabWorkbench` 的 `loop_history` 已有 `prediction_bias`，补"KG 回流 N 条"提示（`kg_feedback` 返回的 `written` 已可用，当前未透传到 `WorkbenchSyncResponse`）。

**改动清单**：`app/services/kg/graph_query.py`（过滤+排序）、`app/api/kg.py`（stats）、`app/api/experiments.py`（sync 响应透传 `kg_written`）、`frontend/src/components/KG*.tsx` + `LabWorkbench.tsx`（徽章+跳转）、`tests/test_kg_feedback.py` 扩展。
**价值**：让 P0 的"KG 飞轮"可被用户看见与验证，补齐"自进化"叙事的最后一公里。
**风险**：低 — 只读增强，不改写入。

---

### P2 — 异步链路可观测与取消（research/recommend 冷启动）

**证据**：
- `api/research.py:62 POST /research/recommend` 走 celery（`tasks.research_recommend`），`dispatcher.py` 分发；`baybe/recommend` 已删，同步阻塞已消除。
- 但**无取消/超时可视化**：`tasks.py` 的 SSE `event parse failed → RUNNING 心跳`（`task_progress.py:79`）与前端 `followLoopTask` 轮询在任务超时/取消时仅静默重试，用户看不到"任务已取消/超时"终态。
- 冷启动：`colbert_store`/`chemtools`/`molscribe` 在首次调用时懒加载，首个 `research/recommend` 可能超时（`workflow.py:323` 等降级日志可见）。

**建议**：
1. 后端：`POST /api/tasks/{id}/cancel` 已有骨架，补 celery `revoke(terminate=True)` + `task_outbox` 状态 `cancelled`，SSE 透传 `cancelled` 终态。
2. 前端：`LoopModal`/`ActionsPanel` 对 `cancelled/timeout` 展示明确toast与重试入口；首包超时文案从"转圈"改为"模型冷启动中…"。
3. 观测：`GET /api/tasks/{id}` 增加 `elapsed_ms` 与 `stage`（已在 `task_progress` 中，补透传）。

**改动清单**：`app/worker/tasks.py` + `app/api/tasks.py`（cancel）、`app/worker/task_progress.py`（终态）、`frontend/src/store.ts`（followLoopTask 取消）、`tests/test_tasks_cancel.py`。
**风险**：低 — 增强可观测，不改主链路。

## 2. 执行顺序

1. **P1 owner Phase 2 硬校验** — 安全基线，开关式不影响现部署，先做。
2. **P1 KG provenance 可观测** — 让 P0 成果可验证，与 Phase 2 并行无冲突。
3. **P2 异步取消/可观测** — 体验优化，最后做。

## 3. 风险矩阵

| 项 | 技术风险 | 业务价值 | 优先级 |
|---|---|---|---|
| owner Phase 2 硬校验 | 中（鉴权开关，兼容旧库） | 高（多租户就绪） | P1 |
| KG provenance 可观测 | 低（只读增强） | 高（飞轮可验证） | P1 |
| 异步取消/可观测 | 低 | 中（体验） | P2 |

## 4. 验证标准

- owner Phase 2：`FORMUMIND_MULTI_USER=true` 时 `alice` 不能 `GET/PUT` `bob` 的 campaign（403），`owner_id IS NULL` 旧行放行；`FORMUMIND_MULTI_USER` 未设时行为不变。
- KG provenance：`measured` 证据在图谱详情可见徽章，点击回跳台账；`GET /api/kg/feedback/stats` 返回 `measured_performance` 计数与 `loop_history` 一致。
- 异步：`cancel` 后 SSE 收到 `cancelled` 终态，前端停止轮询并提示。

## 5. 不做

- 不新增表；`owner_id` 与 `extraction_method` 复用现有列。
- 不改 `FORMUMIND_MULTI_USER` 默认值（保持单 token 单机可用）。

*证据扫描 2026-08-28，未改动代码。*
