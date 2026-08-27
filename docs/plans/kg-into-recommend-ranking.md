# 升级计划：第二优先 — KG 证据落地到配方推荐排序（打分因子）

- **日期**：2026-08-26
- **方向来源**：第一优先（实测驱动闭环）已合入 `d23d67f`
- **关联文档**：`docs/plans/closed-loop-chemical-constraints.md`（第一优先，已推送）
- **状态**：待用户确认后实施

---

## 0. 代码事实核查结论（实施前逐文件核查）

| 问题 | 核查结果 | 结论 |
|------|---------|------|
| KG 在推荐链路是否参与排序？ | hybrid 主路径：`llm.recommend` → `validate_recommended_formulas`（仅字段/CAS/权重校验）→ `ground_recommended_formulas` → **`finalize_recommendation_bundle` → `_score_and_validate`** | ❌ KG 不参与打分 |
| KG 在推荐里是否存在？ | `_relation_insights`（formulations.py:225）查 `get_entity_relations` 生成 `relation_insights` | ✅ 仅**展示性** insights，不影响 score |
| 打分核心入口？ | `_score_and_validate`（workflow.py:67）→ `predictor.predict_full` + `chemtools.screen_formulation(chem_screen)` + `predictor.multi_objective_score` | ✅ 有 `chem_screen` 钩子（推荐路径专用，优化闭环不进） |
| 第一优先的 KG 模块可复用？ | `kg_chemical_check.check_formulation_chemistry(form)` 已存在，确定性、零 LLM | ✅ 直接复用 |
| 评分降权机制是否有先例？ | `predictor.multi_objective_score` 产出 `form.score`；`chemtools.screen_formulation` 返回 warnings | ✅ 加 warning + 降权 score 是既有模式 |

### §0.1 关键定位
- **hybrid 主推荐路径确实经过 `_score_and_validate`**（formulations.py:196 → recommend_pipeline.py:97），且 `chem_screen=True`。
- `chem_screen` 分支注释明确"only on recommend paths — never inside optimization loops"——**正是 KG 约束推荐的专属钩子**，与第一优先（DOE 生成层 `infeasible` 标记）分工互补：推荐层=排序加权（软），优化闭环层=候选标记（硬）。
- KG 关系目前**只产生 `relation_insights` 展示字段**，是"展示型"而非"决策型"——这正是第二优先要修的缺口。

---

## 1. 升级目标

把 KG 材料相容性关系（INHIBITS/SYNERGIZES）从"推荐结果的展示附件"升级为"配方打分的决策因子"：

1. **KG 相容性成为打分因子**：配方骨架含 INHIBITS 关系 → 降权 `form.score` + 加 warning，使其在排序中沉底（软约束，不删除，保持透明）
2. **SYNERGIZES 轻微加权**（可选）：配方含已知协同材料对 → 轻微加分，鼓励已知良好组合
3. **与第一优先互补**：推荐层软加权 + DOE 生成层硬标记，两层都消费同一 KG 源

---

## 2. 架构影响图

```
当前 hybrid 推荐打分：
  _score_and_validate(form)
    ├─ predictor.predict_full        → 性能预测
    ├─ validate_formulation          → 字段校验
    ├─ chemtools.screen_formulation  → 专利/受控化学品(chem_screen)
    └─ predictor.multi_objective_score → form.score

升级后：
  _score_and_validate(form) [chem_screen=True]
    ├─ ...(同上)...
    └─ ⚡ kg_compat_score(form)       ← 复用 kg_chemical_check
         ├─ INHIBITS 命中 → form.score *= 惩罚系数 + warning
         └─ SYNERGIZES 命中 → form.score *= 轻微加成(可选)
```

---

## 3. 文件变更清单（最小改动集合）

| # | 文件 | 改动 |
|---|------|------|
| K1 | `backend/app/pipeline/workflow.py` | `_score_and_validate` 的 `chem_screen` 分支末尾调用 `kg_compat_adjust(form)`：用 `kg_chemical_check.check_formulation_chemistry` 检测结果调整 `form.score` 并加 warning |
| K2 | `backend/app/services/kg_recommend_score.py` | **新建**（或并入 kg_chemical_check）：`kg_compat_adjust(form)` —— 封装"INHIBITS 降权 / SYNERGIZES 加成"逻辑，KG 关闭时 no-op |
| K3 | `backend/app/domain/schemas.py` | `Formulation` 可选加 `kg_compat: dict | None = None` 字段，记录 KG 调整明细（透明可追溯） |
| K4 | `backend/tests/test_kg_recommend_score.py` | **新建**：mock KG INHIBITS → 验证 score 被降权 + warning 出现；KG 关闭 → score 不变 |
| K5 | `backend/tests/test_recommend_kg_ranking.py` | **新建**：端到端推荐排序验证（含 INHIBITS 的配方沉底） |

> 前端无需改动（第一优先已加 infeasible 徽标/告警；推荐层降权通过 `score` 字段自然体现于排序，`relation_insights` 已展示 KG 关系）。如需要，可在 F 阶段补"KG 相容性影响说明"，但非必需。

---

## 4. 实施步骤

1. **K2**：新建 `kg_recommend_score.kg_compat_adjust(form)`：
   - `chk = check_formulation_chemistry(form)`
   - 若 `not chk.feasible`：`form.score *= settings.kg_inhibits_penalty`（默认 0.5）；`form.warnings.append("知识图谱：材料不相容 " + reasons)`
   - 若 `chk.feasible` 且无 INHIBITS，但 `SYNERGIZES` 命中（扩展 check 返回 synergies）：`form.score *= settings.kg_synergizes_bonus`（默认 1.05，可选，默认关）
   - KG 关闭 → 直接返回（no-op）
   - 记录明细到 `form.kg_compat`
2. **K1**：`_score_and_validate` 的 `chem_screen` 分支末尾加 `kg_compat_adjust(form)`（仅在 `chem_screen=True` 时，避免污染优化闭环）
3. **K3**：`Formulation.kg_compat` 字段
4. **K4/K5**：测试
5. **验证**（停 dev 服务避锁）：
   - 后端 `import app.main` OK
   - `pytest tests/test_kg_chemical_check.py tests/test_kg_recommend_score.py tests/test_recommend_kg_ranking.py tests/test_recommend_*.py`
   - 端点 `/api/formulations/recommend`（hybrid）返回 200，含 INHIBITS 的候选 score 显著低于无冲突候选
   - 前端 `tsc` 零错误（若有 F 改动）
6. **提交 + 推送**（用户决策④延续：提交后推送）

---

## 5. 风险矩阵

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| KG 关系稀疏导致几乎不触发 | 中 | 低（退化为仅第一优先） | 软约束；KG 覆盖随 ingestion 增长 |
| 降权过猛导致好配方沉底 | 中 | 中 | 惩罚系数默认 0.5（温和）；仅"沉底"不"删除"，用户仍可见 |
| 误判（KG INHIBITS 是条件性不相容） | 中 | 中 | 降权而非硬排除；warning 明示原因；用户可忽略 |
| 影响优化闭环（误入打分路径） | 低 | 高 | 仅在 `chem_screen=True` 分支调用，优化闭环不传 chem_screen（已确认） |
| SYNERGIZES 加成引入偏差 | 低 | 低 | 默认关闭（`kg_synergizes_bonus=1.0`），需显式开启 |
| 与 relation_insights 重复查询 | 低 | 低 | 复用同一 `kg_chemical_check`，不额外查 KG；insights 展示不变 |

---

## 6. 回滚方案

- 全部增量增强（复用第一优先模块 + 新增 adjust 函数 + 可选字段）
- 回滚：`git revert <commit>`
- 降权可通过 `settings.kg_inhibits_penalty=1.0` 即时关闭（无需改码）

---

## 7. 验收标准（DoD）

- [ ] `kg_compat_adjust` 复用 `kg_chemical_check`，KG 关闭时 no-op
- [ ] hybrid 推荐路径（`_score_and_validate` chem_screen）注入 KG 降权
- [ ] 含 KG INHIBITS 的配方 `score` 被降权且 `warnings` 含原因
- [ ] 不含冲突的配方 score 不受影响
- [ ] 保留第一优先的 DOE 生成层 infeasible 标记（两层互补，互不破坏）
- [ ] 后端 import OK；新增测试通过；核心推荐测试无回归
- [ ] `/api/formulations/recommend`（hybrid）返回 200，排序体现 KG 相容性
- [ ] 前端 tsc 零错误（若做 F 改动）

---

## 8. 实施确认（沿用第一优先决策框架）

1. 化学约束来源：**复用第一优先的 KG 模块**（确定性、零 LLM 成本）
2. 约束强度：**软降权（排序沉底）**，与优化闭环的硬标记互补
3. SYNERGIZES 加成：默认关闭，需显式开启
4. 提交后：**推送远端**（沿用决策④）
