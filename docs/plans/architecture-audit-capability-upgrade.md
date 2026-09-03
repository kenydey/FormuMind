# FormuMind 三大能力升级 — 严苛架构评估（现状审计 + 候选工具裁决）

- **状态**：待评审
- **日期**：2026-09-02
- **立场**：严苛架构师——杜绝缝合怪。每引入一个依赖必须填补真空，否则否决。

## 一、三大能力现状审计（代码级实测）

### 1. 文献检索与问答 — 成熟度高，缺「精」不缺「全」
**已有**：ColBERT 语义检索 + BM25 混合（hybrid_search）+ HyDE 查询扩展 + 多角度子问题检索 + 证据合并去重 + LLM 精排（rerank）+ KG 实体解析增强 + Tavily→SerpAPI→DDG 联邦检索 + 结构化回答/引证。**591 文档 / 23K chunks / 570K KG mentions**。
**真实弱点**（审计发现）：
- `hyde_expand` 已实现但 **comment 明说 "no callers"**（L194）——HyDE 只对主查询生效，子问题不带（有意的省 token，但回答深度受限）
- 无**引文级**可信度分级（证据只有 relevance 标量，无 source 权威度加权）

### 2. 配方推荐 — 架构好，受 LLM 幻觉 + KB 覆盖限制
**已有**：hybrid 模式（KB 证据 → LLM 合成，叠加非互斥）+ 化学闸门（formulation_gate：CAS 校验/MolJSON smiles 校验/元素守恒/计量比）+ grounded_recommend 反查 + 多样性排序 + KG 兼容评分。**41 材料库 / 结构图→SMILES→相似检索闭环已全通**。

### 3. 寻优收敛 — 栈已现代化，卡在数据量
**已有**：BayBE 0.15（botorch 0.18 内核）+ GP 代理模型 + EI acquisition + 批量推荐 + 多目标 Pareto + Optuna NSGA-II + active_learning（surrogate + EI）。**iterations=24 轮闭环**。
**致命短板（实测）**：训练数据 `experiments=1, measurements=0` —— **GP 在无数据时只能靠先验+预测器伪标签**，收敛曲线是「预测器的回声」而非「实验的真收敛」。

## 二、三候选工具裁决（严苛对比）

### ❌ ProcessOptimizer（novonordisk skopt fork）— **否决**
- **定位**：sklearn 风格贝叶斯优化（GP + EI/LCB），单目标为主
- **与现有栈重叠度**：95%——BayBE/botorch 是它的**超集**（多目标 Pareto、批量、连续+离散混合空间、约束处理全有）。ProcessOptimizer 停更多年（skopt 本体 2021 停滞，此 fork 仅续命）
- **引入代价**：+1 依赖、+1 引擎适配层、与 baybe_engine 双轨并存 → 纯缝合怪
- **裁决**：**不引入**。寻优栈已现代化，问题不在工具在数据。

### ❌ Pyomo — **否决（当前阶段）**
- **定位**：数学规划建模语言（LP/MILP/NLP），需外接求解器（glpk/cbc）
- **理论上能做什么**：配方「给定材料池 → 满足 VOC/成本/计量约束 → 全局最优配比」——混合整数线性规划可解确定性最优，与 NSGA-II 的启发式互补
- **为何否决**：
  1. **约束都是线性的吗？** 非——盐雾/附着力预测是 predictor 的黑盒非线性，无法进 MILP。Pyomo 只能解「线性子问题」（成本/VOC/配比和），而这些 **BayBE 的 ContinuousLinearConstraint 已覆盖**（space_builder 已用）
  2. **材料选择是离散的但材料库只有 41 个** —— 穷举组合规模可控，MILP 的全局最优优势被枚举替代
  3. **无求解器**（glpk 未装），VPS 4 核要再背一个求解进程
  4. 现 NSGA-II + BayBE 已处理组合约束
- **裁决**：**不引入**。当材料库 >200 且配方约束线性化验证过，MILP 才有边际价值。留作 v2 候选。

### ❌ DeepChem — **否决（已装未用 = 需移除）**
- **定位**：深度学习化学（图神经网络、分子性质预测、虚拟筛选）
- **为何否决**：
  1. **无 GPU**（VPS 4 核 E5-2690 v2 无 AVX2，torch 锁 ≤2.3.0+cpu）——GNN 训练在 CPU 上 41 材料/1 实验数据要训到天荒地老
  2. **数据荒**（experiments=1, measurements=0）——DL 需要千级样本，FormuMind 连百级都没有。**装了大炮没炮弹**
  3. **与 RDKit 重叠**：DeepChem 的分子描述符/指纹 = RDKit 已实现的子集（现 predictor 已用 RDKit Descriptors 做特征）
  4. **已装 2.8.0 但零接线**（仅 dependencies.py 登记）——纯占空间
- **裁决**：**卸载**（省依赖 + 消除诱惑）。当实验数据累积 >500 条且有 GPU 时再评估。

## 三、真正该做的升级（不引新依赖，内功优化）

### A. 检索增强（0.5-1 天）— 激活闲置的 HyDE
`research_graph.py` 的 hyde_expand "no callers" 是**明晃晃的未完成接线**。子问题检索也启用 HyDE（2-4 次额外 LLM 调用/查询，换取问答深度）。这是零新依赖的最大检索增益。

### B. 收敛的真实闭环（核心）— 数据飞轮优先于工具
**问题本质**：寻优「收敛」是假的——GP 在无实验数据时优化的是预测器。真实解法：
1. **实验回填闭环**：workbench 的 measurements 落库（当前 measurements=0！）→ GP 吃到真数据。检查 `measurements_adapter` 是否真的把 QC 报告写进 measurements 表
2. **campaign 数据迁移**：campaigns 表 17 行带 sample_refs 但 experiments 只有 1——历史 DOE 结果未进训练库
3. 数据到位后，GP 从「预测器回声」变「实验真收敛」，无需换工具

### C. 推荐质量（0.5 天）— 引文权威度加权
证据评分从单一 relevance 升级为 `relevance × source_authority`（专利 > 期刊 > 博客），LLM 合成时引用高权威源优先。检索链路小改。

## 四、结论

| 候选 | 裁决 | 理由 |
|---|---|---|
| ProcessOptimizer | ❌ 否决 | BayBE/botorch 是超集，双轨=缝合怪 |
| Pyomo | ❌ 否决（v2 候选） | 线性约束已覆盖，41 材料枚举即可 |
| DeepChem | ❌ 否决+卸载 | 无 GPU 无数据，纯占空间 |

**FormuMind 的瓶颈不是工具，是数据 + 未接线代码**。先做 A（激活 HyDE）+ B（测量回填闭环）+ C（引文加权），再评估 Pyomo。**依赖包能力已足够**——缺的是把已有轮子接到数据的轨道上。
