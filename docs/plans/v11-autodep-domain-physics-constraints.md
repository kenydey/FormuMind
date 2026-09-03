# v11 实施计划 — #2 自沉积涂料领域深耕 + #3 配方物理约束

- **日期**：2026-08-30
- **方向来源**：5 大升级方向中的 #2（业务核心）与 #3（核心功能深化），#1 数据质量已交付（`d4a3f62`/`61e6a24`）
- **关联文档**：`docs/plans/data-quality-hardening.md`（#1 已完成）、`docs/plans/closed-loop-chemical-constraints.md`（KG 化学约束已实施 `d23d67f`）
- **状态**：待用户评审
- **设计原则**：**叠加模式**——物理约束作为确定性规则层叠加在已落地的 KG 化学约束之上，不做互斥替代；推荐路径 soft（降级+告警）、DOE 路径 hard（infeasible 标记），与现有 `kg_chemical_check` 双路径同构。

---

## 0. 代码事实核查（实施前逐文件核对，2026-08-30）

### #2 自沉积领域现状

| 能力 | 现状（代码证据） | 缺口 |
|------|----------------|------|
| 领域枚举 | `ProductDomain` 仅 3 个：anticorrosion_coating / degreaser / surface_treatment（`schemas.py:14-19`） | ❌ 无 autodeposition |
| 原料库 | `RAW_MATERIALS`（`domain/knowledge.py`，~300 行）通用钝化/磷化原料 | ❌ 无 FeF₃、HF、酸性稳定乳液、M-PP 系列 |
| 配方模板 | `_anticorrosion_template` / `_degreaser_template` / `_surface_treatment_template`（`knowledge.py:422-492`）+ `TEMPLATE_BUILDERS` 分发 | ❌ 无自沉积骨架模板 |
| 领域语料 | `literature.py:34` `SEED_CORPUS` 按 domain 组织；`data/knowledge/autodeposition_emulsion_suppliers.md` **已存在**（含 BONDERITE M-PP 866R/900/930C/930MU、专利 WO2017117169A1、配方参数表） | ⚠️ 语料在 md 未结构化导入 |
| 领域意图 | `intent.py:26-30` 关键词表（脱脂/磷化/钝化…） | ❌ 无「自沉积/autodeposition/autophoretic」关键词 |
| KG 实体 | 通用金属表面处理实体 | ❌ 自沉积专用原料未入图谱 |

### #3 物理约束现状

| 校验项 | 现状 | 缺口 |
|--------|------|------|
| 重量% 闭环 | ✅ `formulation_gate.py:370-372`（±5%）、`chemistry.py:171`（±0.5%） | — |
| CAS 校验和 | ✅ `formulation_gate.py:28-34` `_cas_checksum_ok` | — |
| 摩尔质量反算 | ✅ `chemistry.py:174-185`（formula→M 比对 2%） | — |
| VOC | ✅ `chemistry.py:186-190` limit 校验 + 分类（250/80 g/L） | — |
| 酸碱冲突 | ⚠️ `chemistry.py:349-357` 仅硬编码 2 酸 2 碱（Phosphoric acid / NaOH / Na₂SiO₃） | ❌ 无酸/乳液/填料系统规则 |
| SVHC | ⚠️ `chemistry.py:338-342` 仅 3 个名（Zinc molybdate / Cerium nitrate / Sodium nitrite） | ❌ 无 RoHS 重金属（Pb/Cd/Hg/Cr⁶⁺）名单 |
| KG 化学相容 | ✅ `kg_chemical_check.py`（确定性 INHIBITS 硬 gate，DOE 循环）+ `kg_recommend_score.py`（推荐软罚） | 依赖 KG 关系覆盖度 |
| 当量比 | ⚠️ `chemistry.py:193-204` `amine_epoxy_ratio` 是**质量比**（名称误导） | ❌ 真当量比（EEW/AHEW） |
| 固含量/水性 | ✅ `chemistry.py:229-299` PVC/CPVC/固含量/水性判定 | ⚠️ 无「固含量+溶剂+水=100」量纲一致性校验 |
| **酸性稳定性** | ❌ 完全缺失 | ❌ **核心缺口：pH 2-4 破乳过滤** |

---

## 1. 升级目标

### #2 自沉积涂料领域深耕
1. **领域枚举扩展**：`ProductDomain.autodeposition_coating`（自沉积涂料），全链路（意图识别→模板→语料→KG→推荐）打通
2. **自沉积原料库**：FeF₃、HF、酸性稳定环氧丙烯酸/聚氨酯乳液、氧化剂、促进剂等入 `RAW_MATERIALS`
3. **自沉积配方模板**：树脂/氧化剂/促进剂/酸/pH 缓冲骨架作为离线推荐先验
4. **领域语料结构化导入**：`autodeposition_emulsion_suppliers.md` → `SEED_CORPUS` + KG 实体 seed，让 RAG/推荐覆盖细分领域

### #3 配方物理约束（确定性规则层）
1. **酸性稳定性约束引擎**（新模块）：pH 2-4 下破乳风险判定——乳液酸耐受表 + 组分组合规则，过滤/降级酸性不稳定的组合
2. **相容性规则表**：树脂/交联剂/催化剂化学相容性规则（RDKit 结构级 + 角色级），与 KG INHIBITS 叠加
3. **合规约束**：RoHS 重金属（Pb/Cd/Hg/Cr⁶⁺）+ REACH SVHC 扩展名单入推荐 scoring（硬禁 + 软罚双路径）
4. **物理量纲一致性**：真当量比（EEW/AHEW）、固含量+溶剂+水=100 闭环校验

---

## 2. 架构图

```
当前推荐/DOE 链路（叠加后）：
                                     ⚡ = v11 新增
  用户需求(Requirement)
    │ intent 识别 domain
    ▼
  ┌─ #2 领域层 ──────────────────────────────┐
  │ ProductDomain.autodeposition_coating     │⚡
  │ RAW_MATERIALS 自沉积原料                  │⚡
  │ _autodeposition_template 骨架            │⚡
  │ SEED_CORPUS 自沉积语料 + KG 实体          │⚡
  └──────────────────────────────────────────┘
    ▼
  LLM 推荐 / offline fallback
    ▼
  ┌─ #3 物理约束层（确定性，零 LLM）───────────┐
  │ ① weight%/CAS/M 反算/VOC（已有）          │
  │ ② kg_chemical_check INHIBITS（已有）      │
  │ ③ ⚡ acid_stability_check（pH 2-4 破乳）  │
  │ ④ ⚡ compatibility_rules（树脂/交联/催化）│
  │ ⑤ ⚡ compliance_check（RoHS/SVHC 扩展）   │
  │ ⑥ ⚡ dimension_check（当量比/固含量闭环）  │
  └──────────────────────────────────────────┘
    ▼ 双路径（与 kg_chemical_check 同构）
  推荐路径：soft → 降级 + warnings + score 罚
  DOE 路径：hard → 候选 infeasible + 原因
```

---

## 3. 文件变更清单

### 3.1 #2 自沉积领域深耕

| # | 文件 | 改动 |
|---|------|------|
| A1 | `backend/app/domain/schemas.py` | `ProductDomain` 增加 `autodeposition_coating = "autodeposition_coating"` |
| A2 | `backend/app/domain/knowledge.py` | ① 自沉积原料入 `RAW_MATERIALS`（FeF₃、HF、酸性稳定环氧丙烯酸乳液、阳离子聚氨酯分散体、H₂O₂、促进剂等，含 role/cas/formula/carrier=aqueous）② 新增 `_autodeposition_template`（树脂/氧化剂/促进剂/酸/pH 缓冲骨架，pH 2-4 浴）③ `MECHANISMS` 增加自沉积机理文案（HF/Fe²⁺ 催化、聚合物酸致凝聚沉积）④ `TEMPLATE_BUILDERS` 注册 |
| A3 | `backend/app/domain/project_spec.py` | `_DOMAIN_LABELS` 增加「自沉积涂料」；`_LEGACY_LEVERS` 增加自沉积杠杆（如 FeF₃ 0.3-1.5%） |
| A4 | `backend/app/services/intent.py` | `_DOMAIN_KEYWORDS` 增加（自沉积/autodeposition/autophoretic/自泳漆…） |
| A5 | `backend/app/services/literature.py` | `SEED_CORPUS` 增加 autodeposition 条目（从 `data/knowledge/autodeposition_emulsion_suppliers.md` 结构化提取：BONDERITE M-PP 系列、WO2017117169A1 等） |
| A6 | `backend/app/domain/examples/__init__.py` | 增加自沉积示例 requirement（pH 2-4、铁基底、VOC 低） |
| A7 | `backend/app/services/llm.py` | domain 中文标签映射 + 推荐 prompt 中自沉积机理句（`llm.py:1365-1372` 同类） |
| A8 | `backend/app/services/ip_analysis.py` | 专利检索 query 增加自沉积关键词组 |
| A9 | 新增 `backend/tests/test_autodeposition_domain.py` | 意图识别/模板生成/原料解析/离线推荐冒烟 |
| A10 | `backend/app/db/campaign_store.py` 等默认 domain 点 | 核查 `anticorrosion_coating` 硬编码默认（`:611/:806` 等）——保持默认不变，仅确认新 domain 不破坏 |

### 3.2 #3 物理约束

| # | 文件 | 改动 |
|---|------|------|
| B1 | 新增 `backend/app/services/acid_stability.py` | 酸性稳定性引擎：① 乳液酸耐受表（环氧丙烯酸/阳离子 PUD/纯丙烯酸 → 最低稳定 pH）② 组分组合规则（强碱+酸、碳酸盐填料+酸→CO₂、胺中和剂在 pH<4 质子化失效）③ `check_acid_stability(form, ph_target)` 返回 `(stable, reasons[])`，零 LLM |
| B2 | 新增 `backend/app/services/compliance_rules.py` | RoHS 重金属名单（Pb/Cd/Hg/Cr⁶⁺ 化合物名+CAS）+ REACH SVHC 扩展名单（≥15 项，含来源注释）；`check_compliance(form)` |
| B3 | `backend/app/domain/chemistry.py` | ① `equivalent_ratio(form)`：EEW/AHEW 真当量比（环氧树脂环氧当量、胺固化剂活泼氢当量，原料表新增 eew/ahew 字段）② `dimension_closure(form)`：固含量+溶剂+水=100 一致性 ③ `_ACID/_BASE` 名单扩展 + 酸-乳液规则 |
| B4 | `backend/app/domain/knowledge.py` | 原料表补充 `eew`（环氧当量）/`ahew`（胺氢当量）/`acid_tolerance_ph`（乳液）/`heavy_metal`（合规标记）元数据 |
| B5 | `backend/app/domain/formulation_gate.py` | `validate_formulations` 串联 B1/B2/B3：推荐路径 soft（warnings + 记录 `form.physical_constraints` 字段） |
| B6 | `backend/app/services/engines/baybe_engine.py` | DOE 候选 gate 叠加 `acid_stability_check` + `compliance_check`：命中 → `run.infeasible = True` + 原因（与 `kg_chemical_check` 并列，`baybe_engine.py:250-259` 同类） |
| B7 | `backend/app/services/engines/native_doe_engine.py` / `pydoe_engine.py` | 同一 hard gate 复用（若候选组装结构一致则仅 baybe 为入口，核查后定） |
| B8 | `backend/app/api/loop.py` / 收敛报告 | `chemical_feasibility` 字段扩展为 `physical_constraints`（含 acid/compliance 计数） |
| B9 | 新增 `backend/tests/test_acid_stability.py` + `test_compliance_rules.py` | 各 6-8 例：pH 2-4 破乳命中、强碱+酸拦截、RoHS 拦截、当量比计算、soft/hard 双路径 |
| B10 | `backend/tests/test_formulation_gate.py` 等 | 回归补断言：新校验器接入后既有配方 warnings 不漂移 |

### 3.3 前端（按需，最小改动）

| # | 文件 | 改动 |
|---|------|------|
| C1 | `frontend/src/api.ts` | 收敛报告/推荐响应类型加 `physical_constraints` 字段（透传，非破坏） |
| C2 | `frontend/src/components/DoeResultsPanel.tsx` / `LoopModal.tsx` | infeasible 原因已展示（v10 已做 KG 版），确认 acid/compliance 原因同通道透出即可，通常无需新组件 |

---

## 4. 实施步骤（时间表）

| 阶段 | 任务 | 关键文件 | 验证 |
|------|------|---------|------|
| **S1** | A1-A4：domain 枚举 + 原料 + 模板 + 意图 | schemas/knowledge/project_spec/intent | `pytest tests/test_autodeposition_domain.py` 绿；`import app.main` 成功 |
| **S2** | A5-A8：语料结构化 + 示例 + prompt + 专利 query | literature/examples/llm/ip_analysis | 离线推荐自沉积需求返回骨架配方；语料进 SEED_CORPUS |
| **S3** | B1-B2：acid_stability + compliance 模块 | 新增 2 文件 | 单测 12+ 例全绿；零 LLM 调用（grep 验证） |
| **S4** | B3-B4：当量比/量纲 + 原料元数据 | chemistry/knowledge | `equivalent_ratio(DGEBA+胺)` 数值正确；量纲闭环通过 |
| **S5** | B5-B8：gate 串联（soft）+ DOE 硬 gate + 报告字段 | formulation_gate/baybe_engine/loop | `test_loop_kg_constraints.py` 回归；acid 命中候选被标记 infeasible |
| **S6** | 全量回归 + 前端 tsc + 端到端 | 全部 | **全量 pytest 全绿**；`tsc --noEmit` 零错误；浏览器实际点击验证推荐/DOE 展示原因 |
| **S7** | 提交（按 fix/feat 分 commit）+ 推送 main | — | git log 清晰；SSH push 成功 |

改动估算：后端 ~12 文件（2 新增模块 + 2 新增测试文件 + 8 修改），前端 1-2 文件，总量 +800~1200 行。

---

## 5. 风险矩阵

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| ProductDomain 扩展破坏既有默认 domain 逻辑 | 中 | 高 | 全部默认值（`campaign_store:611/806`、`workbench_training:88` 等）**保持 anticorrosion 不变**；S6 全量回归兜底 |
| 酸性稳定性规则误杀真实可行配方 | 中 | 中 | **叠加模式**：soft 路径仅降级+告警不删除；DOE hard gate 仅在规则明确命中（如强碱+酸、碳酸盐+酸）时触发，模糊命中走 warn |
| RoHS/SVHC 名单过时或误判 | 中 | 中 | 名单含 CAS + 来源注释（EU 官方/行业清单），规则文件独立可维护；误判仅降级不硬禁（除明确重金属） |
| 当量比元数据缺失致计算退化 | 中 | 低 | 原料表缺 eew/ahew 时该组分跳过、整体返回 None（graceful，同 `cpvc` 模式） |
| 乳液酸耐受表覆盖不全 | 高 | 低 | 缺表条目默认「未知→不拦截」；表随文献 ingestion 增长（BONDERITE M-PP 数据源已备） |
| 语料结构化导入质量 | 中 | 低 | 从 md 提取仅取明确字段（产品名/CAS/pH 范围/专利号），不确定字段留空不臆造 |
| DOE 硬 gate 过严致无候选 | 低 | 中 | gate 仅标记不硬删（同 KG 版设计）；报告区分 infeasible 与 warn |
| 前端展示改动回归 | 低 | 低 | 仅透传新字段，复用 v10 既有 infeasible 展示通道 |

---

## 6. 回滚方案

- 全部为**增量增强**：新增 2 个约束模块 + 枚举扩展 + 模板新增，不删改既有路径
- `ProductDomain` 枚举扩展对存量数据零迁移（SQLite 存的是字符串值，旧值不受影响）
- 回滚：`git revert <commit>`；约束可经 settings 开关（`kg_enabled` 同类机制：`physical_constraints_enabled`）灰度
- 新原料/新模板仅在 domain=autodeposition 时生效，不影响既有三域推荐结果

---

## 7. 验收标准（DoD）

- [ ] `ProductDomain.autodeposition_coating` 全链路可用：意图识别 → 模板生成 → 离线推荐
- [ ] 自沉积原料（≥10 项）入 `RAW_MATERIALS`，含 role/cas/carrier=aqueous，FeF₃/HF/酸性稳定乳液在内
- [ ] `_autodeposition_template` 生成 pH 2-4 浴骨架配方，weight% 闭环
- [ ] `autodeposition_emulsion_suppliers.md` 结构化内容进入 `SEED_CORPUS`（BONDERITE M-PP + 专利）
- [ ] `acid_stability.check_acid_stability` 确定性运行（零 LLM），pH 2-4 破乳组合被标记
- [ ] `compliance_rules.check_compliance` 拦截 RoHS 重金属（Pb/Cd/Hg/Cr⁶⁺）
- [ ] `equivalent_ratio` 返回真当量比（非质量比）；`dimension_closure` 固含量+溶剂+水=100
- [ ] 推荐路径 soft（warnings+降级）、DOE 路径 hard（infeasible+原因）双路径验证
- [ ] 后端 `import app.main` 成功；新增测试全绿；**全量 pytest 无回归**；前端 `tsc` 零错误
- [ ] 浏览器实际点击端到端验证（推荐 + DOE 收敛报告展示物理约束原因）
