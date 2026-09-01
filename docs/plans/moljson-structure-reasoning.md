# MolJSON 接入方案评估与推荐 — 结构级 LLM 推理的中间件选型

- **日期**：2026-08-30
- **背景**：用户已用 MolScribe 解决输入端（图片→SMILES），现欲引入 MolJSON 解决推理端「大模型结构盲区」（配方推荐 / 反应推理中的幻觉：数错碳、搞错官能团连接）。
- **状态**：待用户评审
- **关联代码**：`worker/tasks.py:1043`（MolScribe 任务）、`services/ocsr.py`（predict_smiles_molscribe）、`services/chemtools.py:683`（func_group_summary）、`services/llm.py:1010-1055`（推荐 prompt 的官能团块）、`agents/chemist.py:52`（RDKit 游离 NCO 检查）、`services/chem_extract.py:176`（文本级反应抽取）、`domain/formulation_gate.py`（物理约束 gate）

---

## 0. 事实核查

### 0.1 MolJSON 是什么（2026-05 牛津 OXPIG 论文 arXiv:2605.01822）
- 分子图的**显式 JSON schema**：`{atoms: [{id, element}], bonds: [{a, b, order}]}`，原子用唯一 ID + 元素符号，键用原子 ID 对 + 键级
- 设计动机：SMILES 是线性化遍历、IUPAC 是命名规则——都是「给程序/人看」的紧凑格式，LLM 推理时易错；MolJSON 把图直接铺平
- 与 LLM **结构化输出模式天然兼容**（JSON schema 约束）
- 工程现状：`github.com/oxpig/MolJSON`（主包，依赖 RDKit）+ `MolJSON-data`（数据/分析），BSD-3-Clause，IUPAC→SMILES 需 OPSIN 2.9.0（Java）

### 0.2 论文实证（GPT-5 / Claude Haiku 4.5，78,045 题）
| 任务 | MolJSON | SMILES | IUPAC |
|---|---|---|---|
| IUPAC→表示（翻译） | **71.0%** | 43.7% | — |
| 约束生成 | **95.3%** | 64.0% | 76.3% |
| 最短路径推理 | **98.5%** | 92.2% | 82.7% |
- 系统性错误集中在**原子计数与环复杂度**——正是 SMILES 紧凑语法的盲区
- ⚠️ **未测试 DeepSeek**（用户用的是 deepseek-v4 系列）——MolJSON 对 DeepSeek 的增益需实测验证，不能照搬结论

### 0.3 FormuMind 现状（代码证据）
| 环节 | 现状 | 是否「裸 SMILES 直喂」 |
|---|---|---|
| 输入端 | MolScribe 容器化，图片→SMILES（worker 独立队列） | — |
| RDKit 中间层 | `chemtools.func_group_summary`：SMILES→官能团摘要→LLM prompt；`chemist._has_free_isocyanate`：RDKit 结构检查 | **否，已有结构化预处理** |
| 推荐链路 | `llm.py:1047-1055`：材料 (name, smiles) → 官能团摘要块（≤8 项，每项 ≤6 官能团） | 否（已摘要化） |
| 原料库 SMILES | 14 条小分子（DGEBA/IPDA/封闭异氰酸酯/MBT），聚合物乳液无 SMILES | — |
| 反应推理 | `chem_extract.py`：仅文本级 A+B→C 抽取；**无结构级断键/产物预测/机理推理** | — |

---

## 1. 对「另一 AI 三步方案」的逐条评估

| 步骤 | 对方建议 | 评估 | 结论 |
|---|---|---|---|
| 第一步：多模态输入（MolScribe + RDKit 校验） | MolScribe 领衔，RDKit 校验合法性 | **FormuMind 已实现**（MolScribe 已上线 20/20，RDKit 已在 chemist/chemtools 用） | ✅ 无需做 |
| 第二步：MolJSON 中间件 | 全部 SMILES/名称统一转 MolJSON | **方向正确、有论文支撑**，但「全部转换」过度——FormuMind 聚合物/无机盐无 SMILES；全部转会爆 token | ⚠️ 需收敛作用域 |
| 第三步：下游推理（微调化学大模型 / Agent） | 微调模型做反应推理 + 配方推荐 | **与用户硬约束冲突**：用户明确「不做自建、用 DeepSeek API」。微调不在选项内 | ❌ 改为「prompt + 结构化输出」 |
| 进阶：OpenChemIE / Mathpix | 整页反应流程图解析 | OpenChemIE 是 MolScribe 超集但模型更大（VPS 无 GPU 硬约束）；Mathpix 是商业 API（用户偏好免费/自建）；**且 FormuMind 的文档源多为无全文 OA 的专利/文献（已被强制过滤），反应流程图输入稀少** | ⏸ 远期评估 |

---

## 2. 关键判断：MolJSON 对 FormuMind 值不值？

**值得，但作用域必须缩小**——三个理由：

1. **FormuMind 的核心分子大多没有 SMILES**：聚合物乳液（环氧/丙烯酸/聚氨酯）、无机盐（FeF₃/HF/锆盐）——MolScribe 和 MolJSON 都只对小分子结构式有意义。可转换对象 = 配方中的**小分子活性物**（固化剂、交联剂、助剂、单体），约 14 条且增量有限
2. **配方推荐的主要信息不是结构**：相容性/配比/工艺/合规靠名称+CAS+属性+知识图谱，结构只是辅助——MolJSON 对推荐链路的边际增益小
3. **MolJSON 真正的主场是结构级推理**：断键/成键、产物预测、机理问答——**而 FormuMind 目前没有这类任务**。要发挥 MolJSON 价值，先要有结构推理任务

**结论：MolJSON 是「为未来结构推理铺路」的基础件 + 现在就能用的「识别结果校验件」，不是配方推荐的救星。**

---

## 3. 推荐方案（分阶段，先验证后投入）

### P0 — 最小验证（~1 天，先做这个）
**MolJSON 转换模块 + DeepSeek 增益实测**
- 新增 `services/moljson.py`：SMILES→MolJSON（RDKit `MolFromSmiles` + 原子/键遍历，~80 行，BSD-3 参考实现）
- 新增评估脚本 `tests/test_moljson.py` + 离线 benchmark：复刻论文「数原子/官能团识别/环计数」子集，**DeepSeek 直喂 SMILES vs MolJSON** 对比准确率
- 判定门槛：DeepSeek 上 MolJSON 增益 ≥ 论文趋势的一半才继续 P1/P2；否则止步（省得为 0 增益加复杂度）
- **产出**：一份「DeepSeek × MolJSON」实测报告

### P1 — 识别结果校验闭环（若 P0 达标，~1-2 天）
**MolScribe 输出 + LLM 输出的 SMILES 双重校验**
- MolScribe 识别后：SMILES→MolJSON→回读 RDKit，识别误差（缺原子/错键）在进入推理前暴露，前端标注「识别置信度」
- LLM 推荐配方时输出的 `smiles` 字段：同样过 MolJSON 回读校验，非法结构直接拒收并提示修正（现有 formulation_gate 的延伸）
- 收益：**结构零误差保障**，正是用户的核心诉求

### P2 — 结构级推理（独立立项，需另出方案）
- 基于 MolJSON 的「机理问答/产物预测」模块（DeepSeek + 结构化输出）
- 依赖 P0 实测结论；涉及新 API 端点 + 前端面板，需单独评审

---

## 4. 架构图

```
输入端（已有）              中间件（P0/P1 新增）             推理端
┌──────────────┐   SMILES   ┌──────────────────┐   MolJSON  ┌──────────────┐
│ MolScribe    │───────────▶│ services/moljson │───────────▶│ DeepSeek LLM │
│ (图片→SMILES)│            │ SMILES→MolJSON   │            │ (结构化输出)  │
└──────────────┘            │ + RDKit 回读校验  │            └──────────────┘
                            └──────────────────┘
       原料库 (name, smiles) ──────────▶ P1: 推荐输出 smiles 校验
       14 条小分子                       P2: 机理问答/产物预测（未来）
```

---

## 5. 文件变更清单

| # | 文件 | 改动 |
|---|------|------|
| C1 | `backend/app/services/moljson.py`（新增） | SMILES→MolJSON 转换 + RDKit 往返校验（`smiles_to_moljson` / `moljson_to_rdkit` / `validate_smiles`），~80 行 |
| C2 | `backend/tests/test_moljson.py`（新增） | 转换正确性（已知分子：DGEBA/IPDA/MBT）、往返一致性、非法 SMILES 拒收 |
| C3 | `backend/tests/benchmarks/`（新增脚本） | DeepSeek × SMILES vs MolJSON 增益实测（数原子/环计数/官能团识别，~30 题子集） |
| C4 | （P1）`backend/app/services/ocsr.py` | MolScribe 结果后置校验：SMILES→MolJSON→回读，标注置信度 |
| C5 | （P1）`backend/app/domain/formulation_gate.py` | LLM 输出 smiles 字段校验（非法拒收 + 修正提示） |
| C6 | （P2）新 API + 前端 | 独立立项，另出方案 |

---

## 6. 实施步骤（时间表）

| 阶段 | 任务 | 验证 |
|------|------|------|
| P0-S1 | moljson.py 转换模块 + 单测 | 已知分子往返一致 |
| P0-S2 | DeepSeek benchmark 脚本 | 实测报告产出 |
| P0-S3 | **决策门**：增益达标？ | 达标→P1；不达标→停（仅保留 moljson.py 工具） |
| P1-S1 | MolScribe 输出校验闭环 | 识别误差暴露 + 置信度标注 |
| P1-S2 | 推荐 smiles 字段校验 | formulation_gate 拒收非法结构 |
| P1-S3 | 全量回归 + 真实端到端（含一张真实结构图） | 全绿 + 实测闭环 |
| — | 提交推送（feat 分 2 commit：P0 / P1） | SSH push main |

---

## 7. 风险矩阵

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| **DeepSeek 对 MolJSON 无增益**（论文只测 GPT/Claude） | 中 | 高 | P0 决策门：先实测再投入，不达标即止，成本仅 1 天 |
| MolJSON token 开销大（每原子一个 JSON 对象） | 高 | 中 | 只转**关键小分子**（固化剂/交联剂/助剂），聚合物/无机盐不转；func_group_summary 已限 8 项 × 6 官能团 |
| 全量转换引入复杂度/回归 | 中 | 中 | 作用域收敛 + P0 决策门；moljson.py 为纯函数独立模块，不侵入既有链 |
| 转换与 RDKit 版本兼容（backend 镜像 torch 2.13+cpu / MolScribe 独立镜像 torch 2.3） | 低 | 低 | RDKit 已装（chemtools 在用）；MolJSON 转换在 backend 镜像即可，不涉及 MolScribe 镜像 |
| 用户预期「配方推荐立刻变好」落空 | 中 | 高 | 方案明示：配方推荐的主信息非结构，MolJSON 价值在结构推理与校验；P0 报告用数据说话 |

---

## 8. 验收标准（DoD）

- [ ] `smiles_to_moljson` 对 DGEBA/IPDA/MBT 等已知分子往返一致，非法 SMILES 拒绝
- [ ] DeepSeek benchmark 报告产出：SMILES vs MolJSON 准确率对比（数原子/环计数/官能团）
- [ ] **决策门**：增益达标才实施 P1（不达标则止步，仅保留工具模块）
- [ ] （P1）MolScribe 识别结果带置信度校验，误差可暴露
- [ ] （P1）推荐输出非法 smiles 被 formulation_gate 拒收
- [ ] 全量 pytest 无回归；真实端到端含一张真实结构图
