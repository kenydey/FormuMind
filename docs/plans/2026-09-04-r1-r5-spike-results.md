# R1-R5 Spike 实测结果(2026-09-04)

S0 四项 spike 的实测数据与结论, 供 R1-R5 实施依据存档。

## 1. R4 冷启动构成(打点定位, 推翻两轮假设)

| 假设 | 实测 | 结论 |
|---|---|---|
| rdkit import 慢 | `from rdkit import Chem, Descriptors` 冷 import 1.03s(+AllChem 1.27s) | ❌ 排除 |
| `_molecular_features` 慢 | 8 成分 × 8 descriptor 求值 0.01s | ❌ 排除 |
| thermo 数据库初始化 | `Chemical("water")` 首次 6.6-7.5s, 之后 ethanol/isopropanol/acetone 各 0.02-0.03s | ✅ 数据库核心初始化一次 ~7.5s |
| 查不到成分名的失败查询 | `Chemical("Waterborne epoxy resin")` **10.59s ERR**(thermo 反查后失败) | ✅ 真凶: 1-2 个复杂名 = 20s+ |
| 缓存行为 | 同名字第二次 0.02s; **失败也缓存**(热态 predict 全链 0.08s) | ✅ 进程内缓存含失败 |

**结论**: 首个 predict 9-29s = thermo 数据库初始化(~7.5s, 一次)+ 复杂成分名单次失败查询
(~10.6s/个, 名字相关无法预热)。预热收益 = 消固定 7.5s + rdkit 1.5s; 名字相关成本首次
仍付但失败即缓存, 同配方重复 predict 0.08s。
**实施**: `warm_predict()`(guard 幂等 + 失败静默)挂 uvicorn lifespan(后台线程)+
celery `worker_process_init`(prefork 每子进程)。实测: uvicorn 2.3s done(部分热),
worker 2 子进程各 3.4s done。

## 2. R2 因子盘点(数学层约束可达性)

- **数值路径** `baybe_space_builder.build_searchspace`: 因子全为
  `NumericalContinuousParameter`(DOEFactor 只有 low/high 连续型), 已有总量
  `ContinuousLinearConstraint`(wt% sum ≤ max, L79-85)——**BayBE 已有线性约束**。
- KG gate/physical gate 拦截的是**成分语义级互斥**(骨架成分如 Zinc dust + 酸性浴),
  非因子取值——**数值空间无离散点可排除**, `DiscreteExclude` 不适用, 化学互斥
  数学层不可达。→ 2b 收敛为 gate 占比度量(实测 0 基线, 持续 >30% 才研究替代)。
- **genome 路径** `build_genome_searchspace`: 材料选择是 categorical(候选池);
  **注释自证 "Only a third of the seed catalog carries SMILES today"** →
  SubstanceParameter(跨材料泛化 surrogate)被 SMILES 覆盖度卡住。**连带发现**:
  R5 结构回填不只服务 formulation_similarity 指纹, 还解锁 baybe 跨材料泛化。
- **2c 关闭证据**: `run_optimization` L373-375 已 `bounds = predictor.default_bounds(objectives)`
  种子预锁定(注释完整说明 empty-bounds 同分 bug), 逐轮扩张单调——审查"震荡"失真,
  "预锁定"已存在, 无需新增。

## 3. R3 正则词表边界(误伤评估)

- 安全入表(强列举, 分析问法不含): `盘点|综述|汇总|一览|清单|筛出|什么牌号|哪些厂|哪些家`
- 高歧义不入正则(留给 LLM 层判): `总结|整理|对比`——"总结这个配方的耐盐雾"是
  semantic 分析, "帮我整理这批实验数据"是分析——入正则即误伤。
- 触发面: 仅正则未决且 ≥12 字的长句才调 LLM(3s deadline), 高频简单问法零开销。

## 4. R5 影响面扩大(供独立工单)

除 formulation_similarity 指纹外, SMILES 结构入库还解锁:
1. baybe `SubstanceParameter`(genome 空间跨材料泛化——现 1/3 目录覆盖率不足被
   categorical 兜底);
2. 结构级检索/相似度(替代词法 0.15 兜底)。
数据来源决策(molbloom 批量/PubChem PUG REST/人工录入)与 kg_entities schema
(smiles/cas 字段现状)为 5a 前置。
