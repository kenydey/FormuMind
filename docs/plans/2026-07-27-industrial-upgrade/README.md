# FormuMind 工业级升级路线图 — 对标并超越 ChemCopilot Enterprise / Alchemy Cloud

> 本轮交付：**全景路线图，不改动代码**。部署形态：**内部单团队**（故 RBAC/多租户/电子签名不进路线图，但研发溯源所需的版本谱系与计量规范保留）。逆向设计边界：**允许换料**。

---

## Context

用户希望 FormuMind 达到 ChemCopilot Enterprise（AI-Native 逆向配方 + 材料替代建模）与 Alchemy Cloud（多因子闭环优化 + 自动化数据中台）的工业级能力，并提供了 Google Gemini 的一份升级建议供参考。

本文档基于对代码库的**逐文件实测勘探**（后端 32,077 LOC / 163 模块 / 83 个 API 路由 / 128 个测试模块；前端 11,612 LOC / 34 组件 / 6 个 store slice）给出：① 对 Gemini 建议的事实校正；② 真实差距分析；③ 分阶段升级方案。

**核心结论**：FormuMind 的底座质量远超一般开源项目（Adapter+Fallback 贯穿、651 测试全绿、Outbox 幂等、Alembic 迁移、SSE 流式、9 家 LLM 适配）。但存在**一个被所有人忽略的架构性锁死**，它同时是"逆向弱"、"材料替代做不了"、"Pareto 只是摆设"三个问题的**共同根因**。解开它，三大目标能力一次性打通。

---

## 第一部分：对 Gemini 建议的事实校正

诚实起见，先说明 Gemini 报告中与代码实际不符的部分——这些如果照做，会浪费工期在已完成的事情上。

### ❌ 已经做完 / 判断有误的 4 项

| Gemini 的判断 | 代码实际 | 证据 |
|---|---|---|
| "缺乏帕累托前沿，需新增" | **Pareto 前沿早已实现**：`compute_pareto_mask()` 是完整的 O(n²) 非支配排序，自动对 `minimize` 目标翻转符号，输出 `pareto_frontier_ids` + 4 种场景选择（best_performance/lowest_cost/balanced/low_voc） | `backend/app/services/tradeoff_analysis.py:45` |
| "打通 formulation_gate.py 和 tradeoff_analysis.py" | **早已打通**，由 `finalize_recommendation_bundle()` 串联：gate 校验 → 去重 → MMR 多样性 → Pareto 分析 | `backend/app/services/recommend_pipeline.py:117` |
| "前端打包必须剥离 CDN 依赖" | **已经 100% 本地化**。`index.html` 无任何 `<link>` 标签；全 `src/` 无 CDN 域名；字体是系统字体栈；KaTeX/AG Grid 的 CSS 与 woff2 均由 Vite 从 `node_modules` 打进 `dist/` | `frontend/index.html`（12 行）、`frontend/src/index.css` |
| "完善 LLM 重试机制" | **已实现**：`_LLM_RETRY` tenacity 装饰器（认证错误不重试、超时指数退避），配套 `test_llm_tenacity.py`，前端 `DegradedBanner.tsx` 已做降级提示 | `backend/app/services/llm.py`、`frontend/src/components/DegradedBanner.tsx` |

> 附带说明：Gemini 引用的 `app/kg/`、`app/services/inverse_design.py`、`app/domain/tradeoff_analysis.py`、`app/pipeline/recommend_pipeline.py` 四个路径**均不存在**（真实路径分别是 `app/services/kg/`、不存在、`app/services/tradeoff_analysis.py`、`app/services/recommend_pipeline.py`）。

### ⚠️ 判断偏乐观的 1 项

**"agents/ 具备规划-执行-检查的智能体协同雏形"** —— 实际是**一次性线性扇出**，不是智能体循环。`InitializeAgent.review()` 用一个列表推导调用 2 个规则检查器，无消息传递、无重新调用、无协商；LLM 只被允许改写 `message` 文案，**不能改变裁决**（`chemist.py:_llm_explain`）。

真正有迭代的地方在别处：`research_graph.py::_run_claim_check_loop`（最多 2 轮报告重写）和 `auto_loop.py`（RMSE 平台期检测）。

### ✅ Gemini 判断正确的 3 项（但根因比它说的更深）

1. **正向强、逆向弱** —— 正确，且根因不在"没有 inverse_design.py"，见下文第二部分。
2. **图谱有余、推理不足** —— 正确，且更严重：`kg_enabled` **默认关闭**（`config.py:238`），`graph_query.py` 的三个核心函数（`find_path`/`discover_substitutes`/`get_entity_relations`）**零内部调用方**，只能通过 REST 触达，没有任何 recommender/optimizer/agent 消费它。
3. **数据孤岛** —— 正确，且更严重：全库 **11 张表、0 个 ForeignKey、0 个 `relationship()`**，孤儿数据在结构上必然产生。

### 🔍 Gemini 完全遗漏的 6 项

| # | 遗漏项 | 严重度 |
|---|---|---|
| 1 | **配方拓扑锁死**（下文详述）—— 三大目标的共同根因 | 🔴 阻塞级 |
| 2 | **`RAW_MATERIALS` 是 Python 字面量**（约 32 条，`knowledge.py:31`），不是数据库表：不可运行时扩展、无版本、无供应商/货期/法规状态字段 | 🔴 阻塞级 |
| 3 | **计量数据无单位、无方法、无规格限**：`ExperimentRow.measured` 是裸 `dict[str,float]`（`db/models.py:52`）。工业 QC 数据脱离"方法（ASTM B117 vs ISO 9227）+ 单位 + 规格上下限 + 判定"是没有意义的 | 🔴 阻塞级 |
| 4 | **无配方版本谱系**：无法回答"v3→v4 改了什么、为什么、谁改的"。配方管理本质是版本控制 | 🟡 高 |
| 5 | **多模态结构化数据被搁浅**：`vision_extract` → `has_performance` KG 边里已经有真实浓度和实测值，却**永远到不了** `experiments` 表和 predictor（且默认关闭） | 🟡 高 |
| 6 | **前端测试覆盖 1/45 文件**（唯一一个是 `CitationRenderer.test.tsx`），而后端有 651 个测试。`api.ts` 1562 行零测试 | 🟡 高 |

---

## 第二部分：真实根因 —— 配方拓扑锁死

这是整份分析最重要的发现。

### 现象

`pipeline/reconstruct.py:34-43` —— 优化循环里把因子值还原成配方的唯一入口：

```python
base = knowledge.baseline_formulation(requirement)   # 硬编码模板
for ing in base.ingredients:                          # 只遍历模板已有成分
    new = ing.model_copy(deep=True)
    if new.name in overrides:
        new.weight_pct = round(raw, 4)                # 只改 wt%
    ings.append(new)
```

**它只能改配比，不能增删换任何一个成分。** 配方的"拓扑"由 `knowledge.baseline_formulation()` 的硬编码模板固定死。

### 后果：系统被劈成互不相通的两半

| | LLM 路径 (`llm.recommend_formulations`) | 数值路径 (`run_optimization` / BayBE / active_learning) |
|---|---|---|
| 能提出新成分组合？ | ✅ 能 | ❌ **不能**（拓扑锁死） |
| 严格优化 / 收敛保证？ | ❌ 无（一次性文本生成） | ✅ 有（BoTorch GP-EI / BayBE） |
| 约束满足？ | 提示词软约束 | 仅 box bounds |
| 不确定度？ | 无 | 有（`predicted_std`） |

**两条路都做不了"给定目标性能 → 产出配方"**：LLM 能创造但不能优化，优化器能优化但不能创造。

### 这一个根因解释了三个症状

- **逆向弱** → 逆向设计的本质是"目标 → 组成"，而组成空间被锁在一个固定模板的 wt% 单纯形里，搜索空间是玩具级的。
- **材料替代做不了** → 替代 = 换成分，拓扑锁死意味着**无法仿真替代后的性能**。所以现存替代能力只有 `agents/rules.py:23-35` 里 **4 条硬编码字典**，和一条默认关闭、靠 4 个正则抽取的 KG 路径。
- **Pareto 只是摆设** → `compute_pareto_mask` 是对**已生成的候选列表做事后过滤**，不是 Pareto **搜索**。而且 BayBE 的 `ParetoObjective` 结果在 `run_optimization` 里又被 `multi_objective_score` 加权标量化掉了（`baybe_engine.py:285`），Pareto 结构在到达调用方之前就被丢弃。

### 解法的优雅之处

把"成分选择"变成一等搜索变量后，三大目标由**同一个底座**一次解锁。而且 **BayBE 原生支持这件事**——它有 `SubstanceParameter`（自动用 RDKit/Mordred 计算化学描述符）和 `CategoricalParameter`。当前 `baybe_space_builder.py:build_searchspace()` 只用了 `NumericalContinuousParameter`，扩展它是一个**受控的、有边界的改动，不是重写**。

关键收益：用 `SubstanceParameter` 后，化学相似的材料在代理模型的输入空间里彼此靠近，**GP 能跨材料泛化**——这正是"预测换料后性能变化"能成立的数学基础。

---

## 第三部分：分阶段升级路线图

### 依赖关系

```
Phase 0 材料空间数据化 ──┐
                        ├──> Phase 2 逆向设计引擎 ──┐
Phase 1 配方基因组 ─────┘                          ├──> Phase 5 闭环硬化
                        └──> Phase 3 材料替代引擎 ──┘
Phase 4 实验数据脊柱 ────────────────────────────────┘
                                                    └──> Phase 6 前端与质量
```

Phase 0/1 是**基石**（无用户可见功能，但解锁一切）。Phase 4 与 0/1 **正交，可并行**。

---

### Phase 0 — 材料空间数据化（基石）

**目标**：`RAW_MATERIALS` 从 Python 字面量升级为可查询、可扩展、带工业元数据的材料库。

**改动**
- 新增 `materials` 表 + `db/material_store.py`（照 `db/product_store.py:212` 的 `norm_key` 模式写）
- 用现有 32 条字面量做种子迁移，**Day-1 行为零变化**（`knowledge.RAW_MATERIALS` 保留为读缓存视图）
- 新增替代/采购所需字段：`functional_class`（环氧/异氰酸酯/胺/磷酸盐…）、`equivalent_weight`、`hansen_d/p/h`（溶度参数，决定相容性）、`hlb`、`supplier`、`lead_time_days`、`availability`（in_stock / restricted / **discontinued**）、`regulatory`（svhc/reach/rohs）、`substitute_group`
- 新增 `POST /api/materials`、`GET /api/materials`，支持项目级覆盖
- Alembic 迁移（照 `db/alembic/versions/0010_*` 模式）

**复用**
- `domain/formulation_gate.py:86 _resolve_fields()` —— 已有的 catalog→`chemical_lookup`→CAS 复查→`chemtools` 五级级联，直接用来自动补全新录入材料的 CAS/SMILES/分子式
- `services/compounds.py:enrich_materials()` —— PubChem 富集
- `services/chemtools.py:enrich_material_specs()`

**验收**：`GET /api/materials` 返回全部材料含新字段；新增一条材料后，推荐/DOE/优化全链路可用；651 个后端测试全绿。

---

### Phase 1 — 配方基因组（基石，解开拓扑锁）

**目标**：让"成分选择"成为可搜索变量。

**改动**
- 新增 `domain/genome.py`：
  ```
  Slot   = (role, material_id, wt_pct, locked: bool)
  Genome = list[Slot]  + 约束：Σwt% = 100
  ```
- `genome_from_formulation()` / `formulation_from_genome()`
- `pipeline/reconstruct.py`：**保留** `formulation_from_factors()` 不动（所有现有调用方向后兼容），**新增** `formulation_from_genome()`
- 按角色构建候选材料池（`role` + `functional_class` + `carrier` 兼容性过滤）
- 扩展 `services/engines/adapters/baybe_space_builder.py`：混合搜索空间
  - `SubstanceParameter(name=f"slot_{role}", data={material: smiles})`
  - `NumericalContinuousParameter(name=f"wt_{role}", ...)`
  - 保留现有 `ContinuousLinearConstraint` 的 Σwt% 约束

**关键设计 —— 可行性闸门内置于循环**

每个 genome→formulation **在被打分之前**必须通过既有闸门：
`ChemistAgent.inspect()`（返回 `intercept` 即淘汰）→ `validate_formulations()` → `chemistry.full_safety_check()`

这直接实现了 Gemini 提到的"异常检测拦截"，但位置更对：**在搜索循环内部拦截**，而不是等 AI 推荐完了再在送达 `LabWorkbench` 前拦——不可行的基因组从一开始就不消耗预算。

**复用**：`agents/supervisor.py:InitializeAgent.review()`、`domain/formulation_gate.py:validate_formulations()`、`services/doe_anomaly.py`（现有 126 行，此处接入）

**验收**：给定一个基线配方，genome 编解码往返无损；BayBE 混合空间能产出**成分集不同**的候选；被 ChemistAgent 判 `intercept` 的基因组不进入打分。

---

### Phase 2 — 逆向设计引擎（目标一：ChemCopilot 级）

**目标**：输入目标性能区间 → 输出帕累托前沿上的一组**结构各异**的候选配方。

**改动**
- 新增 `services/inverse_design.py`：`design(TargetSpec) -> InverseDesignResult`
- `TargetSpec` 区分两类（这是现在缺失的语义）：
  - **硬约束**（必须满足）：VOC ≤ 250 g/L、pH ∈ [8,10]、成本 ≤ X —— 作为搜索约束，违反即淘汰
  - **软目标**（尽量优化）：max 耐盐雾、min 成本 —— 作为 Pareto 目标
  - 现状是二者混在 `ObjectiveSpec.weight` 里，只当评分权重用（`predictor.multi_objective_score:336`），从不作为搜索必须满足的约束
- 搜索引擎三级降级（沿用全仓 Adapter+Fallback 纪律）：
  1. BayBE 混合空间 + `ParetoObjective`（**不再事后标量化**）
  2. Optuna NSGA-II over genome
  3. numpy 随机重启爬山（永远可用）

**🔑 差异化设计 —— LLM 播种 + MOBO 精修**

这是超越两个对标产品的关键组合：

```
llm.recommend_formulations()  →  N 个化学上合理的起始基因组（探索先验）
            ↓
    MOBO over genome           →  收敛到帕累托前沿（利用）
            ↓
    TradeOffAnalysis           →  场景化选型 + 置信度
```

- ChemCopilot 偏纯 LLM：有创造力，无收敛保证
- Alchemy Cloud 偏纯 DoE：能收敛，但只在人给定的因子空间里
- **FormuMind = LLM 提供化学先验的初始种群 + 贝叶斯优化提供收敛保证**，两者互补。这在架构上是现成的——`llm.recommend_formulations` 和 `BaybeCampaignEngine` 都已存在，缺的只是把前者的输出喂给后者当种子（`baybe_engine.py` 已有 `surrogate_measurements_from_plan()` 冷启动机制，可直接改造）

**输出**：真 Pareto 集（扩展 `compute_pareto_mask` 支持多层前沿 rank-2/3，现在只有 0/None）+ 复用现有 `TradeOffAnalysis`

**API**：`POST /api/design/inverse`（202 异步，复用 `TaskManager.submit_*` + SSE `/api/tasks/{id}/stream`）

**验收**：给定"耐盐雾 ≥1000h、VOC ≤250、成本 ≤40 元/kg"，返回 ≥3 个成分集**不同**的候选，全部满足硬约束，且互不支配；离线无 BayBE 时降级到 numpy 仍能出结果。

---

### Phase 3 — 材料替代引擎（目标二：供应链断供响应）

**目标**：某原料断供/涨价/被管控时，给出带**预测性能偏离度**的替代方案。

**改动**
- 新增 `services/substitution.py`：`find_substitutes(formulation, target_ingredient, constraints) -> SubstitutionReport`
- **三路信号融合排序**：
  1. **结构相似** —— 复用 `chemtools.mol_similarity()`（RDKit Morgan/Tanimoto，`chemtools.py:503`）+ `functional_class` 匹配 + Hansen 溶度参数距离
  2. **性能偏离**（⭐ 核心差异化）—— 把候选材料换进 genome，用 `PropertyRegistry.predict_all()` 预测**每个指标的 Δ**。输出的不是"这个分子长得像"，而是"换成它，耐盐雾 −8%、成本 −22%、VOC +5%"
  3. **文献证据** —— 复用 `graph_query.discover_substitutes()` 的 KG `substitutes` 边 + 引文
- **供应链触发**：材料标记 `availability=discontinued` → 自动扫描所有受影响配方 → 主动推送替代建议（这才是 Gemini 说的"供应链断供建模"的完整闭环）
- 顺手修 `services/kg/graph_query.py:210-211` 的 bug：桥接跳请求了 `synergizes` 链接类型，却在循环里对非 `substitutes` 一律 `continue`，导致协同桥接从未生效
- 开启 KG 语义层（`kg_enabled` 默认值评估）并让 `graph_query` 有真实内部消费方

**API**：`GET /api/materials/{id}/substitutes`、`POST /api/formulations/substitute`

**前端**：新增 `MaterialSubstitutionModal.tsx` —— 左右对照分子结构（复用 `MarkdownMessage.tsx` 里已动态引入的 `smiles-drawer`）、Δ性能条形图、成本/货期/法规徽章

**验收**：把 `Desmodur BL 3175` 标记为 discontinued → 系统列出受影响配方 + 排序替代品，每个带全指标预测 Δ 与置信区间；无 RDKit 时降级到 functional_class + 角色匹配。

---

### Phase 4 — 实验数据脊柱（目标三：Alchemy Cloud 级数据中台）

**目标**：让 FormuMind 成为团队唯一事实来源（SSOT）。与 Phase 0/1 正交，**可并行推进**。

#### 4.1 关系完整性

- 为全部 11 张表补 `ForeignKey` + `relationship()`（当前 **0 个**）
- 修 `doe_plans.experiment_id` 的类型错配：声明为 `String(36)`，而 `experiments.id` 是 `Integer`（`db/models.py:305`）
- Datalab 为 SSOT 的表保留软引用，但加**校验与孤儿检测**（复用 `db/reconciliation.py:134` 的对账模式）

#### 4.2 计量规范化（工业 QC 的地基）

用 `measurements` 表替代裸 `measured: dict[str,float]`：

| 字段 | 说明 |
|---|---|
| `metric` / `value` / `unit` | 指标 + 值 + **单位** |
| `method` | **ASTM B117 / ISO 9227 / GB/T 1771** —— 没有方法的耐盐雾数据不可比 |
| `instrument` / `operator` / `measured_at` | 溯源 |
| `spec_min` / `spec_max` / `passed` | **规格限与判定** |
| `source_document_id` | → `source_documents.id`（FK） |

#### 4.3 QC 报告 ↔ 实验硬绑定（当前完全缺失）

- 新增 `experiment_attachments` 表：`experiment_id` FK → `source_documents.id` FK
- `/api/qc/analyze` 当前是 **42 行占位符**，无条件返回 `defects=[], engine="placeholder"`。改造为真实管线：
  ```
  上传检测报告(PDF/图片) → parsing.py 解析 → vision_extract.py 表格抽取
      → LLM 结构化提取 → Measurement 行 → 绑定到指定 experiment
  ```
- **打捞搁浅的多模态数据**：`vision_extract` → `structural_extractor` 产出的 `has_performance` KG 边里已经有真实浓度与实测性能，却永远到不了 `experiments`。新增桥接：KG 边 → `ExperimentRecord`

#### 4.4 配方版本谱系

- 新增 `formulation_versions` 表：`parent_version_id`（自引用 FK）、`change_summary`、`created_by`、不可变快照
- 回答"v3→v4 改了什么、为什么"——研发溯源的刚需（单团队场景下不需要电子签名，但需要谱系）

**复用**：`services/ingest_tx.py:ingest_document_tx()`（单事务原子入库模式）、`services/vision_extract.py:222`、Alembic 迁移模式

**验收**：上传一份盐雾测试 PDF → 自动抽出带单位/方法/规格限的 `Measurement` 行 → 硬绑定到指定实验 → 该数据直接进入 `training.registry` 训练；删除源文档时 FK 约束阻止产生孤儿。

---

### Phase 5 — 闭环硬化

- **失败样本进闭环**：`auto_loop.py` 当前丢弃失败实验。把失败作为**负样本**同时喂给代理模型和 LLM 提示词，收缩搜索空间（Gemini 提到这点，方向正确）
- **全程多目标**：停止在 `baybe_engine.py:285` 把 `ParetoObjective` 结果标量化
- **异常拦截前置**：`doe_anomaly.py`（现有 126 行）接入 Phase 1 的循环内闸门
- **收敛判据增强**：现有 `rmse_plateau_detected()` 之外，增加超体积（hypervolume）停滞判据

---

### Phase 6 — 前端与工程质量

- **前端测试**：从 1/45 提升 —— 优先 6 个 store slice + `api.ts`（1562 行，前端最大文件）。vitest + RTL 基建已就绪（`vite.config.ts` 内联配置 + `src/test-setup.ts`）
- **打包分片**：`vite.config.ts` 无 `manualChunks`，ag-grid v36 + recharts + katex 全进主 chunk（2.28 MB / gzip 656 KB）。内网环境首屏敏感
- **新增 UI**：`InverseDesignModal`（目标输入 + Pareto 散点 + 候选卡）、`MaterialSubstitutionModal`、`QCReportModal`
- 把 `frontend/scripts/repro-workbench-grid.mjs`（AG Grid 模块注册回归脚本）正式接入 `npm test`

> 注：CDN 本地化**无需改动**——已实测确认零外部依赖。

---

## 第四部分：工作量与风险

| Phase | 内容 | 相对工量 | 风险 | 缓解 |
|---|---|---|---|---|
| 0 | 材料空间数据化 | 中 | 低 | 字面量做种子，Day-1 行为零变化 |
| 1 | 配方基因组 | **大** | **中** | `formulation_from_factors` 原样保留；新路径并存，特性开关灰度 |
| 2 | 逆向设计引擎 | 大 | 中 | 三级降级；离线必须可跑 |
| 3 | 材料替代引擎 | 中 | 低 | 复用现有 Tanimoto + KG + predictor |
| 4 | 实验数据脊柱 | 大 | **中高** | FK 补齐需数据清洗；先加校验+孤儿检测，再上约束 |
| 5 | 闭环硬化 | 小 | 低 | 纯增量 |
| 6 | 前端与质量 | 中 | 低 | 纯增量 |

**贯穿性纪律**（与全仓现有哲学一致，不可破坏）：
1. **Adapter + Fallback**：每个新引擎都必须有零依赖离线回退
2. **651 个后端测试全程保持全绿**
3. **特性开关**：每个新能力挂 `config.py` 的 `FORMUMIND_*` 开关，默认行为不变
4. **Alembic 迁移**：所有 schema 改动走迁移，不手改

---

## 第五部分：为什么这条路线能超越两个对标产品

| 维度 | ChemCopilot Enterprise | Alchemy Cloud | FormuMind（升级后） |
|---|---|---|---|
| 逆向设计 | LLM 生成，无收敛保证 | 人给定因子空间内的 DoE | **LLM 播种 + MOBO 精修**：化学先验 × 收敛保证 |
| 材料替代 | 结构相似度检索 | — | **结构 + 预测性能 Δ + 文献证据** 三路融合，含供应链触发 |
| 闭环 | — | 强 | 强 + **失败负样本** + 循环内化学可行性闸门 |
| 数据中台 | — | 强（SaaS） | 强 + **单位/方法/规格限**规范 + 配方版本谱系 |
| 部署 | SaaS | SaaS | **可完全离线内网**（零 CDN、零 GPU、零 API Key 也能跑全链路） |
| 可审查性 | 黑盒 | 黑盒 | **全链路可解释**：预测分层（mechanistic/prior/trained）、引用绑定、拦截理由 |

最后一行是真正的护城河：**离线可运行 + 全链路可解释**。这是 SaaS 竞品在化工企业内网场景下结构性做不到的。

---

## 验证方式（每阶段完成时执行）

```bash
# 后端全量（当前基线：651 passed, 7 skipped）
cd backend && python3 -m pytest -q

# 前端类型检查 + 构建
cd frontend && npm run build && npm test

# 端到端冒烟
python3 scripts/smoke_test.py
python3 scripts/e2e_mg_passivation.py     # 镁合金钝化全流程
```

**阶段性验收断言**（写成测试）
- Phase 0：新增材料后全链路可用；`GET /api/materials` 含新字段
- Phase 1：genome 编解码往返无损；被 `intercept` 的基因组不进入打分
- Phase 2：硬约束 100% 满足；返回候选**成分集互不相同**且互不支配；无 BayBE 时降级仍出结果
- Phase 3：标记材料 discontinued → 受影响配方被列出 + 替代品带全指标预测 Δ
- Phase 4：上传 PDF → 带单位/方法/规格限的 Measurement 落库并绑定实验 → 进入训练；FK 阻止孤儿
- Phase 5：失败样本影响下一轮采样分布（可断言搜索空间收缩）
- Phase 6：store slice + api.ts 有测试；主 chunk 显著下降

---

## 建议的起步顺序

若后续要落地，建议 **Phase 0 + Phase 1 合并为第一个可交付里程碑**（基石层），因为：
- 二者共同解开拓扑锁，是 Phase 2/3 的硬前置
- 对用户不可见，但可以完全不破坏现有行为（新旧路径并存 + 特性开关）
- 完成后，Phase 2 和 Phase 3 可以**并行**推进

Phase 4（数据脊柱）与基石层正交，可由另一条线并行开始。
