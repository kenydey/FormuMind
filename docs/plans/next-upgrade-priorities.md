# FormuMind 下一步升级优先级建议（代码分析驱动）

> 本文基于 `backend/app` 实际代码扫描，每项建议附文件:行号证据。
> 标注「待核实」的项为基于现有证据的高概率判断，需实施前再确认。
> 现状：三期升级（KG 约束接入 DOE / KG 证据排序 / 验证 DOE 闭环）已落地并实测通过。

## 0. 代码现状速览（已确认）

| 能力 | 证据 | 状态 |
|------|------|------|
| KG 约束接入 DOE 生成 | `tradeoff_analysis._verification_doe_for` + `inverse_design` 走 `analyze_tradeoffs(req=req)` | ✅ 已落地 |
| 验证 DOE 前端下发 | `InverseDesignModal.tsx` 验证 DOE 区块 + `adoptDoePlanToWorkbench` | ✅ 已落地 |
| 自适应闭环 | `services/workbench_loop.py`（`should_trigger_loop_after_sync`/`requirement_from_campaign`/`_campaign_loop_context`）+ `tasks.py:795 auto_loop.loop_iterate` + `experiments.py:341` sync 触发 | ✅ 贯通 |
| Sobol/pydoe 设计 | `services/engines/pydoe_engine.py:14 PYDOE_DESIGNS` 含 `sobol` | ✅ 已落地 |
| 逆设计 | `services/inverse_design.py` + `api/design.py` | ✅ 已落地 |

## 1. 升级优先级建议

### P0 — KG 自进化闭环（实测 → KG 回流）【最大卖点缺口】

**证据**：当前 KG 在推荐/搜索时是**单向读取**：
- `services/active_learning.py:43` `knowledge.baseline_formulation(Requirement(...))` —— 仅读 KG 基线
- `recommend_pipeline` / `tradeoff_analysis` 读 `kg_compat` / `kg_evidence` 做约束与排序（已确认）
- **全仓库无"实测收敛结果 → 写回 KG 知识库"的调用**（grep `kg|knowledge` 在 active_learning/loop/sync 中仅出现读操作；`qc_ingest.py:174 measured sync failed` 是写实验表，非 KG）

**缺口**：KG 是 FormuMind 核心资产，但当前是"只读知识库"。配方经推荐→验证 DOE→实测收敛后，**新证据未回流更新 KG**（如某组分组合的实测盐雾值、某约束的实测可行性）。KG 不会随项目推进而"变聪明"。

**建议**：在 `workbench_loop` 收敛一轮后（`loop_history` 追加时，`campaign_store.py:226 _append_loop_history`），调一个 `kg.ingest_measured_evidence(campaign)` 把实测最优配方的性能/可行性写回 KG 实体与关系。复用现有 `services/ingestion.py` 的 KB ingest 管道。

**价值**：KG 从静态→自进化，直接强化"AI 辅助研发"核心叙事；下游推荐约束/排序质量随数据积累提升（飞轮）。
**风险**：KG 写入可能污染基线知识（需区分"文献证据"与"本项目实测证据"，加 provenance 字段）。中风险。
**待核实**：`services/knowledge.py` 是否已有 measured-evidence 写入接口；若无则属新增。

---

### P1 — 推荐/DOE 入口收敛（去冗余 + 异步一致性）

**证据**：
- 三个推荐/DOE 入口并存：
  - `api/research.py:62 POST /research/recommend`（celery 异步，CRAG 研究推荐）
  - `api/formulations.py:121 POST /formulations/recommend`（同步，LLM+KB 配方推荐，含三期 tradeoff/verification_doe）
  - `api/doe.py:176 POST /baybe/recommend`（**同步**，纯 BayBE 引擎 DOE 推荐）
- `baybe/recommend` 是**同步阻塞**（`doe.py:205 engine.recommend(...)` 直接返回），大模型/大候选集下会占住 uvicorn worker；而 `research/recommend` 走 celery。
- `baybe/recommend` 与 `formulations/recommend`/DOE 功能重叠（都生成 DOE 计划）。
- 启动日志 `recover_stalled: unknown operation baybe_recommend — skipped`（`main.py:87`→`dispatcher.py:51`）—— **历史遗留 outbox 行** operation=`baybe_recommend`，但 `dispatcher._dispatch`（`dispatcher.py:33-47`）无该分支，无法恢复（无害脏数据，但说明曾走过 celery 现改同步）。

**建议**：
1. 统一 DOE 生成入口：`baybe/recommend` 要么并入 `formulations/recommend`（KB+LLM 综合），要么也改为 celery 异步（与 research/recommend 一致），消除同步阻塞 + 入口语义重叠。
2. 清理 `dispatcher.py` 对 `baybe_recommend` 的未知操作日志噪音（加分支或清理历史 outbox 行）。

**价值**：符合用户"删冗余入口、统一数据流"偏好；消除同步阻塞风险；降低维护面。
**风险**：前端可能直接调 `baybe/recommend`（需查前端调用点确认影响面）。低风险。
**待核实**：前端哪些组件调 `baybe/recommend`（grep 前端 `baybe/recommend`）。

---

### P1 — 异常透明度审计（37 处 except 降级）

**证据**：`grep "except" ` 命中 37 处 `logger.warning(... exc)`，绝大多数是**合理降级+日志**（如 `workflow.py:323 BayBE failed, falling back to numpy/optuna`、`colbert_store.py:300 falling back to rag store`、`ocsr.py:49 MolScribe predict failed`）。

**风险点**：部分降级可能掩盖真实失败（如 `task_progress.py:79 event parse failed → 降级为 RUNNING 心跳`，`research_graph.py:113 Grade LLM failed` 静默跳过评分）。需逐一确认降级后是否有用户可见提示。

**建议**：审计这 37 处，区分"可恢复降级"（保留）与"应上抛/前端提示"的（补 `error` 状态或 toast）。重点看 `task_progress.py:79`（SSE 静默降级可能让用户看不到任务真失败）与 `claim_checker.py:208`（claim check 失败静默）。

**价值**：提升可观测性，避免"静默失败"误导研发决策。
**风险**：改动分散，需逐个判断。低风险-中。

---

### P2 — 多用户 owner 校验（13 处 TODO，当前单 token 模式）

**证据**：`grep TODO` 命中 13 处，全部是 `添加 owner 校验 — 单 token 模式下暂无法实现，迁移到多用户后需校验`（`experiments.py:288/306/320`、`tasks.py:133`、`_dispatch.py` 注释等）。

**建议**：当前单 token 模式**不紧急**。仅当规划多用户/团队协作时再做。列入"架构债清单"，不排近期。

**价值**：多租户安全。当前 0。
**风险**：涉及鉴权重构。P2。

---

### P2 — 验证闭环的数据质量增强（可选）

**证据**：三期验证 DOE 已生成 `verification_does`，但 `build_doe` 用的 `req` 来自逆设计 `req`（`inverse_design.py:487 analyze_tradeoffs(forms, objectives, req=req)`），**验证 DOE 的 targets 直接取自候选预测值**（`tradeoff_analysis._verification_doe_for` 的 `targets` 拼接 `form.predicted`）。

**建议（可选）**：验证 DOE 下发实测后，建议在 `loop_history` 或 KG 里记录"预测 vs 实测偏差"，用于校准 `form.predicted` 的 surrogate 模型（与 P0 互补）。属增强，非阻塞。

## 2. 推荐执行顺序

1. **P0 KG 自进化闭环** —— 核心卖点，飞轮效应最强（先核实 `knowledge.py` 写入接口）
2. **P1 推荐入口收敛** —— 去冗余 + 消除同步阻塞（先核实前端调用点）
3. **P1 异常透明度审计** —— 质量债，可并行
4. **P2 owner 校验 / 数据质量增强** —— 按业务节奏

## 3. 风险矩阵

| 项 | 技术风险 | 业务价值 | 优先级 |
|----|---------|---------|--------|
| KG 自进化闭环 | 中（provenance 设计） | 高（飞轮） | P0 |
| 入口收敛 | 低（需核实前端） | 中（去冗余/性能） | P1 |
| 异常审计 | 低-中 | 中（可观测） | P1 |
| owner 校验 | 高（鉴权重构） | 当前 0 | P2 |
| 数据质量增强 | 低 | 中 | P2 |

---
*本文所有证据来自 `backend/app` 代码扫描（2026-08-27），未执行任何改动。标「待核实」项需在实施前确认。*
