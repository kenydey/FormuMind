# 升级计划：实测驱动闭环深化 — KG 化学约束 + 跨项目失败记忆接入 DOE 生成

- **日期**：2026-08-26
- **方向来源**：用户认可「第一优先：实测驱动闭环」
- **决策确认**（用户 4 项）：① 化学约束直接对接 KG ② 前端增强一并做 ③ 跨项目失败记忆默认全局 domain 级 ④ 提交后推送
- **关联文档**：`docs/plans/remove-process-optimization.md`（已完成）
- **状态**：待用户确认后实施

---

## 0. 代码事实核查结论（实施前逐文件核查，避免凭空设计）

| 能力 | 现状（代码证据） | 结论 |
|------|----------------|------|
| 实测训练代理模型 | `training.py` 按 (domain, metric) 用 RandomForest/ridge；`predictor._blend_trained` 三层融合 | ✅ 已实测驱动 |
| 实测贯通进 DOE 生成 | `baybe_engine.run_optimization` 每轮 `recommend(measurements=...)` → `measurements_adapter` → baybe `add_measurements` | ✅ baybe 内部 surrogate 吃真实实测重训 |
| 失败记忆 | `failure_memory.py` 已实现 `failed_records(domain, project_id="")` + `penalty_for()`，在 `active_learning.py` 给采集函数加排斥 | ✅ 单 campaign 已用 |
| KG 语义关系 | `kg/relation_extractor.py` 抽取 `SUBSTITUTES/SYNERGIZES/INHIBITS/CORRELATES_*/REQUIRES`；`kg/graph_query.get_entity_relations(entity_id, link_types=...)` 确定性查询 | ✅ **确定性、零 LLM 成本** |
| 化学可制造性 gate | `feasibility.check_formulation` 存在，但内部调 `InitializeAgent().review` = **blocking LLM 调用** | ⚠️ 不可直接用于每候选（成本/延迟爆炸） |

### §0.1 路线修正（关键）

用户要求「化学约束直接对接 KG」。深度核查后发现：
- **KG 当前关系是实体间语义关系（材料A INHIBITS 材料B），没有"条件化可制造性"schema**——但 `INHIBITS` 关系足以表达「材料不相容」，可直接用于 DOE 候选校验。
- `feasibility.check_formulation` 是 **LLM agent 驱动**，对每个 DOE 候选调用会成本/延迟爆炸，**不可作为主路径**。

**修正路线**：化学约束 = **KG 材料关系确定性校验**（查候选配方内材料对的 `INHIBITS`/`SYNERGIZES`），零 LLM 成本、天然泛化；`feasibility` 仅作可选 LLM 兜底（默认关闭）。这既满足「对接 KG」又规避成本陷阱。

---

## 1. 升级目标

自适应 DOE 闭环在**生成候选**时注入两类领域知识，形成"实测 + KG 化学约束 + 跨项目失败经验"三重紧闭环：

1. **KG 化学约束确定性校验接入 DOE 生成**：候选配方内材料对若在 KG 存在 `INHIBITS` 关系，标记 `infeasible` + 原因（如"X 与 Y 文献报道不相容"）。
2. **跨项目失败记忆强制复用**：`failure_memory` 默认跨同 domain 所有项目（含 KG 化学约束），一个自沉积项目的"pH<3 破乳"经验自动排斥下个项目同类候选。
3. **风险感知采样**：`predictor.predict_std` 作为采集函数探索权重（小样本酸性实验优先高信息量点）。

---

## 2. 架构影响图

```
当前闭环：
  台账实测 → training.registry(ML) → predictor._blend_trained
           → baybe_engine.recommend(measurements) → baybe surrogate(吃实测)
           → 候选打分(multi_objective_score) → 返回

升级后闭环（⚡ = 新增）：
  台账实测 → training.registry → predictor(含 std)
           → baybe_engine.recommend
                ⚡ kg_chemical_check(候选)         ← KG 材料 INHIBITS 确定性校验（零 LLM）
                ⚡ failure_memory.penalty_for(候选, 跨项目failures)  ← 组织失败经验排斥
                ⚡ std-aware 采集(EI/UCB 用 predict_std)  ← 风险感知
           → 候选筛选/降级(infeasible 标记) → 返回 + 收敛报告附化学校验
```

---

## 3. 文件变更清单（最小改动集合）

### 3.1 后端（主线）

| # | 文件 | 改动 |
|---|------|------|
| C1 | `backend/app/services/kg_chemical_check.py` | **新建**：`check_formulation_chemistry(form)` → 遍历配方材料对，用 `kg.get_entity_relations` 查 `INHIBITS`/`SYNERGIZES`；返回 `(feasible, reasons[], incompatible_pairs[])`。确定性、零 LLM。 |
| C2 | `backend/app/services/engines/baybe_engine.py` | `recommend`/`run_optimization` 生成候选后调用 `kg_chemical_check` + `failure_memory`；不可制造候选标记 `infeasible` 字段（不破坏 baybe 内部状态） |
| C3 | `backend/app/services/active_learning.py` | `failure_memory` 默认跨项目：`failed_records(project_id="")` 已 domain 级聚合；统一为"KG 化学排斥 + 失败记忆排斥"合并项 |
| C4 | `backend/app/services/failure_memory.py` | `failed_records` 确认默认 `project_id=""` 即 domain 全局聚合（已支持）；新增 `kg_incompatible_pairs(domain)` 桥接 KG（可选） |
| C5 | `backend/app/services/predictor.py` | 确认 `predict_std` 在闭环可用；暴露 `uncertainty` 供采集函数（若 baybe 已内部处理可省略） |
| C6 | `backend/app/api/loop.py` 或 `workbench_loop.py` | 收敛报告增加 `chemical_feasibility` 字段（infeasible 计数 + 原因样例） |
| C7 | `backend/tests/test_kg_chemical_check.py` | 新建：mock KG store 注入 INHIBITS 关系，验证候选被标记 |
| C8 | `backend/tests/test_loop_kg_constraints.py` | 新建：DOE 生成中 infeasible 候选被标记 + 跨项目失败记忆生效 |

### 3.2 前端（按决策②一并做）

| # | 文件 | 改动 |
|---|------|------|
| F1 | `frontend/src/components/LoopModal.tsx` 或收敛报告组件 | 展示每轮候选化学可制造性校验（infeasible 标记 + 原因：如"X 与 Y 不相容"） |
| F2 | `frontend/src/components/DoeResultsPanel.tsx` | DOE 候选列表标注 `infeasible` 徽标 |

---

## 4. 实施步骤

1. **C1**：新建 `kg_chemical_check.py`，用 `kg.get_entity_relations` 确定性查 INHIBITS/SYNERGIZES；与现有 `feasibility.FeasibilityVerdict` 结构对齐（返回 feasible/status/reasons）
2. **C4**：确认 `failure_memory.failed_records(project_id="")` 跨项目默认行为；`kg_incompatible_pairs` 桥接（读 KG store 的 INHIBITS 关系）
3. **C2**：`baybe_engine` 候选生成后插入 `kg_chemical_check` + `failure_memory` 双重 gate（仅标记 `infeasible`，不动 baybe 内部 surrogate）
4. **C3**：`active_learning` 默认启用跨项目失败排斥
5. **C6**：收敛报告加 `chemical_feasibility`
6. **C7/C8 + F1/F2**：补测试与前端
7. **验证**（停 dev 服务避 database is locked）：
   - 后端 `import app.main` 成功
   - `pytest tests/test_workbench_loop.py tests/test_auto_loop.py tests/test_doe_*.py tests/test_kg_chemical_check.py tests/test_loop_kg_constraints.py`
   - 端点：`POST /api/baybe/recommend`、`POST /api/loop/iterate` 正常；含 INHIBITS 材料对的候选被标记 infeasible
   - 前端 `tsc --noEmit` 零错误
8. **提交 + 推送**（决策④）：commit 后 `git push origin main`

---

## 5. 风险矩阵

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| KG 关系稀疏导致约束几乎不触发 | 中 | 低（退化为仅失败记忆） | KG 覆盖随文献 ingestion 增长；约束为"加分项"非"必需项" |
| infeasible 标记过严导致 DOE 无候选 | 中 | 中 | gate 仅"标记+降级"不硬排除；baybe 仍返回全部候选，前端/报告区分 |
| failure_memory 跨项目误排斥（项目间正当差异） | 中 | 中 | 排斥为惩罚项（非硬排除）；`_PENALTY_WEIGHT`/`_KERNEL_LENGTH` 可调 |
| 改动 baybe_engine 影响现有推荐/优化 | 中 | 高 | 仅在候选生成**后**加 gate，不动 baybe 内部 surrogate；新增字段不删旧 |
| predictor.predict_std 冷启动不可靠 | 低 | 低 | std 仅探索加权，不替代实测；小样本退化均匀探索 |
| 误用 feasibility LLM gate 致成本爆炸 | 高（若走错路） | 高 | **明确不默认调 feasibility LLM**；KG 校验为确定性零成本主路径 |

---

## 6. 回滚方案

- 全部为**增量增强**（新增 kg_chemical_check 模块 + 候选标记字段），不删改现有闭环主路径
- 回滚：`git revert <commit>`
- gate 可经 `settings` 开关关闭（`loop_convergence_enabled` 同类机制），便于灰度

---

## 7. 验收标准（DoD）

- [ ] `kg_chemical_check.check_formulation_chemistry` 确定性运行（零 LLM），被 `baybe_engine` 调用
- [ ] 含 KG `INHIBITS` 材料对的候选在 DOE 结果中被标记 `infeasible` 且附原因
- [ ] `failure_memory` 默认跨同 domain 项目复用失败经验
- [ ] 残留引用检查：无 `feasibility` LLM 在闭环热路径被默认调用
- [ ] 后端 `import app.main` 成功；新增测试通过；前端 `tsc` 零错误
- [ ] 核心测试（workbench/auto_loop/doe）无回归
- [ ] 收敛报告含 `chemical_feasibility` 字段

---

## 8. 实施确认（用户已确认）

1. 化学约束来源：**直接对接 KG**（采用 KG 材料关系确定性校验路线，规避 feasibility LLM 成本）
2. 前端增强（F1/F2）：**本次一并做**
3. 跨项目失败记忆：**默认全局 domain 级开启**
4. 提交后：**推送远端**
