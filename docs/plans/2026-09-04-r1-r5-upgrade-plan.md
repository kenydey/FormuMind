# R1-R5 综合升级方案(审查第二轮,2026-09-04)

## 背景与范围

前序已完成(见 `2026-09-04-ai-review-top5-fixes.md` 执行偏差记录):P0 混料降级、P1 矛盾检测
worst/best、P2 伪相似降权、P3 claims 共享池、P4 AL 自适应 std、SSE 流式问答、loop 测试窗口。
本批合并两处输入:① 用户提交的第二份分级清单(P0-P3 共 10 子项);② 对其中逐项实读复核后
判定的真实剩余工作。核对结论(证据见各节):

- **已解决 3 项**(清单滞后于实施):chat_claims 线程、混料 DOE 降级、contradiction 平均陷阱。
- **证据不成立 2 项**(复核否决,重验后维持):graph_query→Neo4j(visited 去重,L100-101,图规模
  千级边,80⁴ 上界伪命题;4 核 VPS 引入 Neo4j 为负优化);arxiv_source AST 降级(docstring L20-31
  明确 per-section 为文档化权衡,无"粗暴正则破坏结构"对应代码)。
- **成立未做**:打包为 R1-R5 如下。**范围边界**:R5 依赖成分结构数据来源决策,本批仅给前置
  路线,不实施(单独立项)。

---

## R1 领域规则配置中心(acid_stability / formulation_linker / chat_clarify)

### 现状(实读)

| 位置 | 硬编码内容 | 后果 |
|---|---|---|
| `acid_stability.py` L54-61 | `_STRONG_ALKALI_PREFIXES`(Sodium hydroxide…)、`_STRONG_ALKALI_EXACT`(Sodium metasilicate…)、`_REACTIVE_METALS`(Zinc dust…),使用于 L119/L130 | 新增碱/金属须改代码;漏判=检查放行不安全配方(baybe recommend L264 等依赖此 gate) |
| `formulation_linker.py` L14-23 | `_ROLE_HINTS` 子串→Role 字典(树脂/固化剂/助剂…),使用于 L27 `_infer_role` | 新助剂类别推断 `unknown`,图谱 Role 错标 |
| `chat_clarify.py` L13+ | `_AMBIGUOUS_TERMS: dict[str, list[tuple[str, str\|None]]]`(水性/快干…歧义候选) | 术语歧义库无法后台维护 |

### 方案(TOML + 加载器 + 硬编码兜底,零新依赖)

1. **载体**:TOML 文件(Python 3.11 stdlib `tomllib` 只读,注释可写、git 可评审;项目
   `.venv` 为 cpython-3.11.14 已确认)。
   - 包内默认规则:`backend/app/data/rules/acid_stability.toml`、`linker_roles.toml`、
     `ambiguous_terms.toml`(内容=现硬编码值的 1:1 迁移,迁移时值不变,行为零漂移)。
   - 外部覆盖:环境变量 `FORMUMIND_RULES_DIR` 指向用户可编辑副本(与 `.env.host` 同层);
     未设置用包内默认。
2. **加载器**:新模块 `app/services/rule_loader.py` — `load_rules(kind) -> dict`,
   `functools.lru_cache` + 进程启动加载;文件缺失/解析失败 → `log_handled_exception` +
   **返回内置兜底常量**(兜底常量即当前硬编码值,从现文件原样搬入)——规则配置化
   永远不破坏现功能。
3. **改造点**(每处把 `_XXX = {...}` 常量替换为 `load_rules(...)` 取数,字典形状保持兼容):
   - `acid_stability.py` L54-61 → `load_rules("acid_stability")` 的 `alkali`/`reactive_metals` 段;
     `_RESIN_ROLES`(L48,参与酸稳定检查的 role 集合)属检查策略而非数据,本轮**留在代码**,
     文档标注。
   - `formulation_linker.py` L14 → `load_rules("linker_roles")["role_hints"]`。
   - `chat_clarify.py` L13 → `load_rules("ambiguous_terms")`。
4. **测试**:
   - 锚定:loader 默认加载结果 == 现硬编码值(逐键断言,防迁移漂移);
   - 覆盖:临时 `FORMUMIND_RULES_DIR` 加新碱(如 Tetramethylammonium hydroxide)→
     `_composition_violations` 命中;新 hint(如 `rheology_modifier`→`additive`)→ `_infer_role`
     返回 additive;歧义词新增条目生效;
   - 兜底:指向空目录/坏文件 → 规则仍可用(内置默认)。

### 风险

低。TOML 迁移 1:1 + 兜底默认 → 行为零漂移;唯一注意:配置值变化需重启进程生效
(规则低频变更,接受;`lru_cache` 不做热重载,避免复杂度)。

---

## R2 BO 引擎强化(baybe_engine.py,两批共指同一引擎)

### 现状(实读)

- `_new_campaign` L164:`BotorchRecommender(n_restarts=1, n_raw_samples=16)` 硬编码,注释自证
  "keeps a single botorch round at a few seconds instead of ~2.5 min(default 10/64)"。
- `recommend` L255-268/304:采样后硬 gate(`check_formulation_chemistry` → `run.infeasible`
  标红),约束未参与采样决策。
- `run_optimization` L394-399:`bounds[m] = (min(lo,val), max(hi,val))` 逐轮扩张——**单调非
  震荡**(复核确认),但归一化尺度随轮次缓慢漂移。

### 方案(分 2a 落地 + 2b 能力边界诚实收敛)

**2a 采集超参自适应(落地)**
- 新函数 `_recommender_for(req, objectives)` 按复杂度选档,默认值经 spike 实测后定:
  - 快档(现状):连续因子 ≤4 且目标 ≤2 —— `1/16` 维持(平滑空间 SLSQP 首起点足够,注释论据成立);
  - 平衡档:高维(连续 >4 或目标 >2)—— `n_restarts=3, n_raw_samples=32`;
  - 档位可被 env `FORMUMIND_BO_QUALITY=fast|balanced|thorough` 覆盖(thorough=`5/64`)。
- **先 spike 测时间-质量曲线**(3 档 × 代表域:自沉积 4-6 因子/单-双目标),实测 qLogEHVI 值与
  耗时,再定默认档。baybe 任务跑在 celery worker(非请求线程),慢档的等待成本与现状不同,
  以实测为准,不臆测。

**2b 约束前移(能力边界收敛,不做假承诺)**
- BayBE 约束接口支持**线性不等式与离散子空间排除**,不支持跨连续因子的任意化学互斥规则
  (如"Zn dust 用量>0 与浴 pH<4 冲突"是非线性交叉约束,`DiscreteExclude` 表达不了)。
- spike 盘点:现 KG gate/physical gate 实际拦截的组合,是否落在**离散 categorical 因子**上。
  - 是 → 在 `build_searchspace`(L154 注入点)加 `DiscreteExclude`,数学层剔除,真前移;
  - 否(连续为主)→ **维持现状采样后硬 gate**,并在 `plan.notes` 记录本轮被 gate 掉 run 占比;
    若占比持续高(度量>30%)才值得研究替代采样策略——把"算力浪费"变成可度量项,而非假装
    BayBE 能表达任意化学规则。

**2c 边界预锁定(轻量增强,可选并入)**
- 把 L394-399 逐 run 扩张改为:打分前先对候选集做一遍纯 `predict` 收集极值 → 锁定
  `bounds` → 再跑 `multi_objective_score`(归一化尺度一轮内恒定)。额外成本=一轮热 predict
  (实测 0.1s/run 量级)。若 2a spike 显示收益不显著,砍掉,不强行做。

### 变更文件

`baybe_engine.py`(+60-80 行)、`tests/test_baybe_adaptive.py`(档位选择/覆盖 env/discrete
排除注入点单测,不跑真实 baybe 慢档——单测用 mock recommender 构造;时间曲线 spike 另录
`docs/plans/` 附档)。

### 风险

中。baybe 版本 API 变动(DiscreteExclude 构造)需 spike 验证;慢档默认值若设太高会让 worker
单任务 2.5min+(可接受但需确认与 celery `-c 2` 队列无冲突);2b 若盘点后无 categorical 互斥,
落地面积收窄为度量+文档,如实交付。

---

## R3 意图路由结构化(entity_resolver.py)

### 现状(实读)

`entity_resolver.py` L19-30:`_ENUMERATIVE_RE` 仅覆盖
`所有|全部|列举|有哪些|哪些.*文献|含.*的|牌号.*有哪些|list all|all formulations`;
`detect_mode` 未命中 → L36-38 `auto → hybrid(有 CAS/商品名)/semantic`。"盘点近年来关于
无铬钝化的综述"类枚举问法必然漏判,降级为普通语义搜索(召回方式错配)。

### 方案(双层:零成本正则扩充 + LLM 短超时结构化,基建复用)

1. **正则快层扩充(先上,零风险)**:`_ENUMERATIVE_RE` 增补
   `综述|整理|汇总|盘点|总结|对比|比较.*差异|有什么区别`(枚举/对比意图),新词表集中
   常量 + 注释来源。**覆盖审查原例**,成本一次 patch。
2. **LLM 结构化兜底(未决时触发)**:`detect_mode` 仍 `auto` 且 query 长度 ≥ 阈值(如 12 字)
   时,调一次 structured output(复用 chat claims 已建立的 `complete_json` 模式):
   `{mode: "enumerative"|"semantic"|"hybrid"}`;**3s 硬超时**(意图分类比 claims 更可降级,
   不给慢窗口机会),超时/失败 → 正则兜底 `auto`(现行为,不劣化)。此路径只在正则未决时
   触发,高频简单问法零 LLM 开销。
3. 模式枚举沿用现有 `RetrievalMode` 值,不加 schema。

### 变更文件

`entity_resolver.py`(+40)、`app/services/llm.py` 若需暴露意图分类用短超时封装(优先复用
`complete_json` 参数)、`tests/test_intent_routing.py`:
① 综述/盘点类问法 → `enumerative`(正则扩充覆盖的断言);② 长句未决问法 mock LLM 返回
`semantic` → 走 semantic;③ LLM 超时/失败 → `auto`,响应时间不劣化(假 LLM 挂起断言降级)。

### 风险

低-中。LLM 兜底路径延迟:3s 上限 + 仅在未决长句触发 + 失败落回现状 → 最坏等于现状。
正则扩充可能误伤("总结报告"类)——词表含"总结"需评估与 semantic 查询的边界,单测锁行为。

---

## R4 predictor 冷启动预热(实测性能债)

### 现状(实测)

打点数据:单次 `predictor.predict` 首次调用 **8.81s**,后续 0.1s/run(一次性初始化)。
连带影响:loop/DOE 任务端到端 17-40s(已因此放宽测试窗,但**用户侧等待真实存在**);
首次 predict 可能出现在 uvicorn 进程或 celery worker 进程(loop 任务在 worker)。

### 方案

1. **先打点定位 8.8s 构成**(predict_full 内部:registry 冷建?descriptor 链?mechanistic 模型
   加载?)——定位后预热目标函数 = 完整 predict 链路;若为模块级一次性缓存,预热收益=
   每进程一次(uvicorn/celery 均长驻,收益成立)。
2. **预热钩子**(幂等、失败静默 log、不影响启动失败语义):
   - uvicorn:并入 `app/main.py` `lifespan` bootstrap 段(L70-77,受
     `FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP` 控制——测试环境自动跳过,现有测试不慢化);
   - celery worker:`worker_ready` 信号回调(或 `start-dev.sh` 启动后触发一次预热请求)——
     worker 进程独立于 uvicorn,必须双挂;
   - 预热输入:默认代表性配方(按 domain 默认 levers 构造,失败静默)。
3. 预热耗时 ~9s 计入启动(后台非阻塞亦可,spike 定:前台等 vs 后台线程——后台线程预热
   存在首请求竞争,前台等更简单可预期)。

### 变更文件

`predictor.py`(+`warm_predict()`,幂等 guard)、`app/main.py`(lifespan +~10 行)、
`app/worker/celery_app.py` 或 `start-dev.sh`(worker 预热)、测试:预热幂等(两次调用
无副作用)、SKIP 环境下不触发。

### 风险

低。预热失败静默;启动 +9s 仅首次;测试环境 SKIP 隔离。

---

## R5 成分结构入库(RDKit 指纹 Tanimoto 的前置,单独立项)

### 为什么不在本批做(实读证据)

`chem_extract.py` L104-116:SMILES 提取是正则从**文本中抓现成 SMILES 串**(RDKit 校验),
**不是名称→结构解析器**;配方成分录入为商品名/化学名,不含 SMILES → 指纹无输入。
P2 已落地的别名归一化+词法降权 0.15 是当前数据条件下的正确上限;指纹需成分级结构数据。

### 前置路线(供决策,不在本批实施)

1. **5a 结构回填管道**:成分名批量 → SMILES(候选源:molbloom/PubChem PUG REST 批量,网络
   一次性入库)→ 存 KG 实体结构字段(cas/smiles,先查 `kg_entities` schema 是否有字段,无则
   迁移)→ **入库后热路径本地读,不再网络**(解决 P2 当初的"热路径 N×M 不可行"顾虑)。
2. **5b 指纹接入**:`formulation_similarity._chemical_name_similarity` 一级改为
   Morgan/Tanimoto(双方都有结构时),别名/词法降为二级兜底——替换本轮"词法 0.15 兜底"。
3. 依赖决策点:结构数据来源选型、回填触发时机(入库时 vs 定期批)、schema 迁移。
   建议独立工单 + 数据量盘点(现有成分名去重后多少条)后再排期。

---

## 架构总览

```
┌─ R1 规则配置中心 ────────────────────────────────┐
│ data/rules/*.toml ──> rule_loader.py(lru_cache)   │
│        ├─ acid_stability(碱/活泼金属黑名单)        │
│        ├─ linker_roles(ROLE_HINTS)                │
│        └─ ambiguous_terms(歧义词典)               │
│  外部覆盖: FORMUMIND_RULES_DIR; 缺失→内置兜底      │
└──────────────────────────────────────────────────┘

┌─ R2 BO 引擎 ─────────────────────────────────────┐
│ req → _recommender_for(复杂度自适应 1/16|3/32|5/64)│
│     → build_searchspace(+DiscreteExclude, 若spike  │
│       确认互斥在 categorical 因子)                 │
│     → recommend → 硬 gate(连续空间能力边界,度量    │
│       被 gate 占比写入 notes)                     │
│ 2c(可选): 打分前预扫描锁定 bounds 归一化尺度        │
└──────────────────────────────────────────────────┘

┌─ R3 意图路由 ────────────────────────────────────┐
│ query → 正则快层(扩充词表)──命中→ enumerative      │
│        └未决长句→ LLM structured(3s 超时)         │
│                    ├成功→ 返回 mode                │
│                    └失败→ auto(现状,不劣化)        │
└──────────────────────────────────────────────────┘

┌─ R4 预热 ────────────────────────────────────────┐
│ lifespan(bootstrap段, SKIP 隔离) + worker_ready    │
│        └→ predictor.warm_predict()(幂等,失败静默)  │
└──────────────────────────────────────────────────┘
```

## 文件变更清单

| 文件 | 操作 | 包 |
|---|---|---|
| `app/data/rules/acid_stability.toml` / `linker_roles.toml` / `ambiguous_terms.toml` | 新增(值=现硬编码 1:1) | R1 |
| `app/services/rule_loader.py` | 新增(加载+缓存+兜底) | R1 |
| `app/services/acid_stability.py` / `formulation_linker.py` / `chat_clarify.py` | 改(常量→loader) | R1 |
| `app/services/engines/baybe_engine.py` | 改(自适应档位/DiscreteExclude 注入/可选预锁定) | R2 |
| `app/services/kg/entity_resolver.py` | 改(正则扩充+LLM 兜底) | R3 |
| `app/services/predictor.py` | 改(+warm_predict) | R4 |
| `app/main.py` / `app/worker/celery_app.py` 或 `scripts/dev/start-dev.sh` | 改(预热挂点) | R4 |
| `tests/test_rule_loader.py` / `test_intent_routing.py` / `test_baybe_adaptive.py` / 预热测试 | 新增 | R1/R2/R3/R4 |
| `docs/plans/2026-09-04-r1-r5-spike-results.md` | 新增(R2 时间曲线、R4 打点结论) | spike |

## 实施步骤时间表

| 步 | 内容 | 预计 |
|---|---|---|
| S0 spike(并行) | R4 打点定位 8.8s;R2 因子盘点 + 3 档时间曲线;R3 正则词表边界评估 | 0.5 天 |
| S1 | **R4 预热**(依赖 S0 打点;uvicorn+worker 双挂) | 0.5 天 |
| S2 | **R3**(先正则扩充 patch,后 LLM 兜底) | 1 天 |
| S3 | **R1**(TOML 迁移 1:1 + loader + 兜底) | 1-1.5 天 |
| S4 | **R2**(2a 自适应 + 2b spike 结论落地/收敛 + 可选 2c) | 1.5-2 天 |
| S5 | 全量回归(相关套件+test_integrations 60s 窗)+ 端到端冒烟 | 0.5 天 |
| — | R5(5a/5b) | 独立工单,待结构数据来源决策 |

每步完成:单测全绿 → 分 commit(main,按 fix/feat/chore 分),push 默认继续。

## 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| R1 配置迁移值漂移 | 低 | loader 默认==硬编码逐键锚定测试;外部覆盖缺失回退内置 |
| R1 配置需重启生效 | 低 | 规则低频变更,接受;文档标注 |
| R2 baybe 慢档拖 worker | 中 | 先 spike 实测再定默认;env 覆盖;档位默认平衡档 |
| R2 BayBE 无法表达连续互斥 | 中 | spike 盘点 categorical 才 DiscreteExclude;否则保留 gate+占比度量,如实交付 |
| R2 baybe API 版本差异 | 中 | spike 验证 DiscreteExclude 构造;单测 mock,不依赖真实 baybe 慢路径 |
| R3 LLM 兜底延迟 | 低 | 3s 短超时 + 仅未决长句触发 + 失败落 auto(等于现状) |
| R3 正则扩充误伤 | 低 | "总结/汇总"边界评估 + 单测锁行为 |
| R4 预热放错进程 | 低 | uvicorn lifespan + worker_ready 双挂;SKIP 环境隔离 |
| R4 预热与首请求竞争 | 低 | 前台等预热(启动 +9s)或后台+guard,spike 定 |
| 整体回归(chat/kg/doe/baybe/integrations) | — | 每包相关套件全绿再提交;baybe 慢档不进 CI 单测 |

*复核基线:全部现状论据来自 2026-09-04 实读代码/实测打点(行号见各节);R5 不实施理由为
chem_extract 能力边界(非懈怠);2b 收敛为 BayBE 数学表达能力的诚实边界,不做假承诺。*
