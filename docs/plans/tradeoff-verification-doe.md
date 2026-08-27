# 升级计划：第三优先 — 多方案推荐的可解释性闭环到实验

- **日期**：2026-08-26
- **方向来源**：第一优先（实测驱动闭环）`9956a13`、第二优先（KG→推荐排序）`9956a13` 已合入
- **关联文档**：
  - `docs/plans/closed-loop-chemical-constraints.md`（第一优先）
  - `docs/plans/kg-into-recommend-ranking.md`（第二优先）
- **状态**：待用户确认后实施

---

## 0. 代码事实核查结论（实施前逐文件核查）

| 问题 | 核查结果 | 结论 |
|------|---------|------|
| 多方案 trade-off 是否存在？ | `analyze_tradeoffs`（tradeoff_analysis.py:131）产出 `TradeOffAnalysis`（candidates / scenario_picks / dominance_notes / pareto_frontier_ids） | ✅ 已存在 |
| "为什么选 A 不选 B"是否已有？ | `dominance_notes`（支配关系说明）已生成 | ✅ 有基础，可强化 |
| DOE 方案生成能力？ | `workflow.build_doe(req, design, n)` 成熟，产出 `DOEPlan` | ✅ 可直接复用 |
| "下发台账"通道？ | 前端 `adoptDoePlanToWorkbench(plan)`（workflowSlice.ts:203）→ 推到 workbench campaign；后端 `/experiments/workbench/campaigns` 创建/列 | ✅ 通道已存在 |
| trade-off 是否带验证 DOE？ | `TradeOffAnalysis` 无"验证 DOE"字段 | ❌ **缺口** |
| 前端 trade-off 展示？ | `InverseDesignModal.tsx` 已有 Pareto 前沿（candidates + pareto_rank）展示 | ✅ 可加验证 DOE 区块 |

### §0.1 关键定位
- **"可解释性"已有半截**：`dominance_notes` 说清支配关系，但**没有"怎么验"**——用户看到"方案 A 优于 B"却不知如何低成本验证 A 的预测性能。
- **"下发台账"通道成熟**：`adoptDoePlanToWorkbench` 能把任意 `DOEPlan` 推到 workbench campaign（且 `workbench_auto_train` 会在保存时自动回灌训练）。第三优先只需把"验证 DOE"构造成 `DOEPlan` 复用该通道，**零新基础设施**。
- **`build_doe` 基于 req levers 生成 DOE**（不接收特定 form 为中心）——验证 DOE 方案采用"围绕该候选基线标注的 lhs 小样本"实现。

---

## 1. 升级目标

把多方案推荐的"可解释性"闭环到实验：

1. **每个 Pareto/Scenario 候选自动生成最小验证 DOE**：基于 `build_doe`（lhs, n=4-6），标注该候选为参考基线，让用户对比"A 的验证 vs B 的验证"哪个先达标
2. **一键下发台账**：每个验证 DOE 带"下发台账"按钮，调用既有 `adoptDoePlanToWorkbench` 推到 workbench campaign
3. **强化"为什么选 A 不选 B"**：`dominance_notes` 已说明支配，新增"验证建议"文案（如"方案 A 在成本 Pareto 前沿，建议优先验证：执行其验证 DOE 确认腐蚀性能 ≥ X"）

---

## 2. 架构影响图

```
当前 trade-off 推荐：
  analyze_tradeoffs(forms, objectives) → TradeOffAnalysis
    ├─ candidates[]（含预测性能）
    ├─ scenario_picks[]（场景最优）
    └─ dominance_notes[]（为什么选 A 不选 B）

升级后：
  analyze_tradeoffs(...) → TradeOffAnalysis
    ├─ ...(同上)...
    └─ ⚡ verification_does[]   ← 新增
         └─ VerificationDoe { candidate_id, note, doe_plan: DOEPlan }
              └─ 由 build_doe(req, "lhs", n=4) 生成，plan.notes 标注参考基线候选

前端 InverseDesignModal：
  每个 candidate 卡片 → 折叠"验证 DOE"区 → "下发台账"按钮
                                  └─ adoptDoePlanToWorkbench(doe_plan)
```

---

## 3. 文件变更清单（最小改动集合）

### 3.1 后端

| # | 文件 | 改动 |
|---|------|------|
| T1 | `backend/app/domain/tradeoff_schemas.py` | 新增 `VerificationDoe(BaseModel)`：`candidate_id: str` / `note: str` / `doe_plan: DOEPlan`；`TradeOffAnalysis` 加 `verification_does: list[VerificationDoe] = []` |
| T2 | `backend/app/services/tradeoff_analysis.py` | `analyze_tradeoffs` 末尾对每个 Pareto 前沿 + scenario_pick 候选调 `_verification_doe_for(form, req, objectives)` 生成 `VerificationDoe`；`note` 写"验证建议" |
| T3 | `backend/app/services/tradeoff_analysis.py`（辅助） | 新增 `_verification_doe_for(form, req, objectives)`：调 `build_doe(req, "lhs", n=settings.verification_doe_n)`，把候选名/预测写入 `plan.notes`，返回 `VerificationDoe` |
| T4 | `backend/app/config.py` | 新增 `verification_doe_n: int = 4`、`verification_doe_enabled: bool = True` |
| T5 | `backend/tests/test_tradeoff_verification_doe.py` | **新建**：mock `build_doe` 返回固定 DOEPlan，验证 `verification_does` 被填充且 candidate 对应；关闭开关 → 空 |

### 3.2 前端

| # | 文件 | 改动 |
|---|------|------|
| F1 | `frontend/src/api.ts` | `TradeOffAnalysis` 加 `verification_does?: VerificationDoe[]`；新增 `VerificationDoe` 接口 |
| F2 | `frontend/src/components/InverseDesignModal.tsx`（或 trade-off 展示组件） | 每个 candidate 卡片加折叠"验证 DOE"区（展示 doe_plan.runs 概览）+ "下发台账"按钮（调 `adoptDoePlanToWorkbench`） |

---

## 4. 实施步骤

1. **T4**：config 加 `verification_doe_n` / `verification_doe_enabled`
2. **T1**：`tradeoff_schemas` 加 `VerificationDoe` + `TradeOffAnalysis.verification_does`
3. **T3**：`_verification_doe_for` 辅助函数（包装 `build_doe` + notes 标注）
4. **T2**：`analyze_tradeoffs` 集成（遍历 Pareto + scenario 候选，跳过 KG-infeasible 候选——复用第二优先的 `kg_compat`）
5. **T5**：测试
6. **F1/F2**：前端类型 + 展示/下发
7. **验证**（停 dev 服务避锁）：
   - 后端 `import OK`
   - `pytest tests/test_tradeoff_verification_doe.py tests/test_recommend_api_tradeoff.py`
   - 端点 `/api/formulations/recommend`（hybrid, include_tradeoff=true）返回 tradeoff.verification_does 非空
   - 前端 `tsc` 零错误
8. **提交 + 推送**（决策④）

---

## 5. 风险矩阵

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| 每候选生成 DOE 成本高（build_doe 慢） | 中 | 中 | 限制 only Pareto 前沿 + scenario_picks（通常 ≤6）；n=4 小样本；开关可关 |
| 验证 DOE 与候选配方"不围绕"它（build_doe 基于 req levers） | 中 | 低 | plan.notes 明确标注"参考基线候选 X"；用 candidates 的预测作为验证目标参考 |
| 前端展示拥挤 | 低 | 低 | 折叠区（默认收起），仅展示 run 数 + 下发按钮 |
| 与现有 adopt 通道冲突 | 低 | 中 | 复用既有 `adoptDoePlanToWorkbench`，不改通道；新 plan 带 `design="verification"` 标记 |
| 过度生成（非 Pareto 候选也生成） | 低 | 低 | 仅 Pareto 前沿 + scenario_picks 生成，普通候选不生成 |

---

## 6. 回滚方案

- 全部增量增强（新 schema 字段 + analyze_tradeoffs 集成 + 前端展示）
- 回滚：`git revert <commit>`
- 生成可通过 `verification_doe_enabled=False` 即时关闭

---

## 7. 验收标准（DoD）

- [ ] `TradeOffAnalysis.verification_does` 在 Pareto 前沿 + scenario 候选上被填充
- [ ] 每个 `VerificationDoe.doe_plan` 是合法 `DOEPlan`（可 `adoptDoePlanToWorkbench` 下发）
- [ ] `doe_plan.notes` 标注参考基线候选名
- [ ] KG-infeasible 候选（第二优先标记）**不**生成验证 DOE
- [ ] `verification_doe_enabled=False` → 空列表
- [ ] 前端展示验证 DOE + 下发按钮，调用既有通道
- [ ] 后端 import OK；新增测试通过；trade-off 核心测试无回归
- [ ] 前端 tsc 零错误

---

## 8. 实施确认（沿用决策框架）

1. 验证 DOE 粒度：**每候选独立最小 DOE**（Pareto 前沿 + scenario_picks），非整体对比 DOE
2. 下发通道：复用既有 `adoptDoePlanToWorkbench`（不新建）
3. 开关：`verification_doe_enabled` 默认开，`verification_doe_n=4`
4. 提交后：**推送远端**（沿用决策④）

### 子方案备选（若用户改选）
- **B：整体对比验证 DOE** — 只生成一个 DOE 同时验证 top-K 候选（每组一个中心点）。更省实验，但"每个方案独立验证"语义弱。
