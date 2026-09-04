# 审查修复方案:Top-5 优化(2026-09-04)

## 背景与复核方法

另一 AI 对 FormuMind 提交 15 项审查(问答 5 / KG 5 / BO-DOE 引擎 5)。
本次全部**逐条实读代码复核**:10 项属实/部分属实,5 项论据失真(B3
graph_query 内存爆炸——visited 去重证伪 80⁴ 上界;A3 LaTeX 降级——
per-section 设计有文档化权衡;A5 chat_context 拼接——有门控仅短问
触发;C2 动态边界——单调扩张非震荡;C4 BO 黑盒化——化学特征经
surrogate 已参与)。以下仅保留复核成立的 5 项,按 影响×真实性×可操作性 排序。

---

## P0:pyDOE 混料设计静默降级 LHS(pydoe_engine.py)

**现状(实读)**
`app/services/engines/pydoe_engine.py` L115-129:
```python
def build_plan_with_fallback(factors, design, n=None) -> DOEPlan:
    if design not in PYDOE_DESIGNS:
        return build_native_plan(factors, design, n=n)      # L121
    try:
        return build_pydoe_plan(factors, design, n=n)        # L124
    except Exception:
        native_design = design if design in {"lhs", "ccd"} else "lhs"   # L126 ← 混料塌缩点
        plan = build_native_plan(factors, native_design, n=n)
        plan.notes = f"engine=native (pydoe fallback); {plan.notes}"
        return plan
```

**确认的问题**:`simplex_lattice` 等混料设计一旦 pyDOE 抛异常(缺函数/参数不合法),静默降级为**无约束 LHS**——"各成分和=100%"的混料数学前提丢失,生成配方总量偏离 100%,研发照方配料即废。`design not in PYDOE_DESIGNS` 的未知设计分支(L121)同样绕过混料语义(未知混料设计名应报错而非走 native 独立因子路径)。

**方案**
1. 引入混料设计集合:`_MIXTURE_DESIGNS = {"simplex_lattice", "simplex_centroid"}`(后者当前不在 PYDOE_DESIGNS——未知混料设计**显式抛错**,前端给提示)。
2. 改 L126 判定:设计 ∈ 混料集合 → 降级目标必须是 native 引擎的混料实现(native_doe_engine 若支持则用,不支持则 `raise ValueError` 显式失败,notes 标注);设计 ∈ {lhs, ccd} 独立因子 → 维持 LHS 兜底。
3. 降级路径统一在 `plan.notes` 写入原始异常原因(现仅写 engine 来源,不写 why)。

**变更文件**:`engines/pydoe_engine.py`(+~20 行)、`engines/native_doe_engine.py`(核查/补混料支持,若无则只改 pydoe 侧)。
**测试**:`tests/test_doe_engines.py` 增 3 例:① mock pydoe.simplex_lattice_design 抛异常 → 断言 `raises`(或 native 混料输出);② 混料降级结果 `sum(w)≈1`(允许容差 1e-6);③ lhs 降级仍走通(不回归)。
**风险**:低。可能影响现有调用方捕获 ValueError 的行为——查 `doe_registry.build_doe_plan` 调用链后确认;不静默是行为变更(从"能出结果"变"报错"),需同步前端 DOE 生成错误提示(已存在通用错误展示)。

---

## P1:矛盾检测跨属性"平均值陷阱"(kg/contradiction.py)

**现状(实读)**
`app/services/kg/contradiction.py` L121-160:
```python
measured_values.append((val, src))          # 跨属性混收: NSS / 光泽 / 附着力…
domain_perf = sum(v for v, _ in measured_values) / len(measured_values)  # L131 全局平均
...
for link in lit_links:                      # 文献链路
    expected_sign = _EXPECTED_SIGN[lit_type]
    deviation = (0.5 - domain_perf) * 2     # 单属性极端被平均稀释 → 漏报
    strength = abs(deviation) * lit_conf
    if strength < threshold: continue        # NSS 0.1 + 光泽 0.9 → 0.5 → 必然跳过
```

**确认的问题**:① 领域实测信号 = 全部属性算术平均,任一单属性极端(防腐 0.1)被其他属性(光泽 0.9)中和 → 与文献"该实体防腐极佳"的矛盾**漏报**;② L158 `measured_property=target` 填的是链路目标实体而非真实物理量,归因错位;③ 无该属性实测时仍用全局平均硬凑。

**方案**
1. 收集期按 `measured_property` 分桶:`{property: [(val, src), …]}`;每桶内取均值(同属性多测点)。
2. 对每条文献链路,用**该链路 link 的语义属性**取桶:`_EXPECTED_SIGN[lit_type]` 对应的属性映射(防腐链路 ↔ 盐雾/腐蚀桶;附着力链路 ↔ 附着力桶)——需在 `kg_schemas` 或本文件加 `_PROPERTY_FOR_LINK` 映射。
3. 无对应桶 → 该链路标 `no_measurement` 状态(不进矛盾标记,也不给假平均),在响应里作为 `unchecked_links` 返回供前端提示"该性能尚无实测"。
4. L158 的 `measured_property` 填真实桶名;`measured_value` 填桶值。

**变更文件**:`kg/contradiction.py`(核心 ~40 行)、`kg_schemas.py`(KGContradictionMark 若需加字段——优先不加,复用现有 `unchecked` 语义,核查响应 schema)、前端展示(若有矛盾列表,补"无实测"灰标——先查 `frontend` 是否渲染该响应,无则后端先行)。
**测试**:`tests/test_kg_contradiction.py` 增:① NSS=0.1+光泽=0.9 + 文献"防腐佳" → **断言命中矛盾**(现实现必漏);② 无对应属性实测 → 断言不进矛盾、进 unchecked;③ 同属性多测点取均值。
**风险**:低-中。可能改变现有矛盾列表输出形态(部分历史矛盾消失、新增正确命中)——回归测试全绿为准;`_EXPECTED_SIGN` 的属性映射需按现有 KG link_type 枚举逐一核对(只映射已出现的类型,未知类型走 unchecked,不臆造)。

---

## P2:配方相似度词法伪相似(kg/formulation_similarity.py)

**现状(实读)**
`app/services/kg/formulation_similarity.py` L49-59:
```python
q_only = q_ings - c_ings;  c_only = c_ings - q_ings
if kg_bonus and q_only and c_only:
    for q_ing in q_only:
        for c_ing in c_only:
            q_parts = set(q_ing.lower().split())          # 词法拆分
            c_parts = set(c_ing.lower().split())
            overlap = q_parts & c_parts
            if overlap and len(overlap) >= min(len(q_parts), len(c_parts)) * 0.3:
                role_score += 0.5 * weight                # 词法重叠加分
```

**确认的问题**:未匹配成分之间用**空格词法 overlap** 当相似度——英文/带空格成分名("Waterborne epoxy resin" vs "Waterborne polyurethane resin" 共享 2/3 词)获 0.5×weight 加分,化学上荒谬;相似度喂给配方复用/推荐(调用方 `find_similar_formulations`),污染下游。

**方案(叠加式,不动已匹配成分的主路径)**
1. 未匹配成分相似度改三级:① 两者都能出 SMILES(经 `chem_extract`/RDKit 解析或 KG 已存 cas/smiles)→ **Morgan 指纹 Tanimoto**(radius=2,2048bit);② 不能出结构但 KG 别名可归一化(`knowledge.resolve_material_name`)→ 归一化后精确匹配(命中=1.0,miss=0);③ 前两者皆失 → 词法 overlap **降权**(0.5→0.15)且阈值 30%→50%(宁缺勿滥)。
2. 单次成分间相似度 >0.6 才计入 `role_score`(指纹 0.6≈Tanimoto 有意义阈值;词法路径不再直接加分)。
3. 新逻辑做成独立函数 `_chemical_similarity(a, b, kg_store)` 便于单测;失败静默返回 0(相似度计算不能打断推荐)。

**变更文件**:`kg/formulation_similarity.py`(+60 行,抽函数)、可能 `knowledge.py`/`chem_extract` 复用(只 import 不修改)。
**测试**:`tests/test_kg_similarity.py`(或并入现有测试文件):① 水性环氧树脂 vs 水性聚氨酯树脂(英文名)→ 指纹路径相似度**显著低于**词法旧值;② CAS 已知成分别名归一化命中;③ 无结构无别名 → 词法低权不超 0.15 上限;④ 回归:已匹配成分路径分数不变。
**风险**:中。相似度数值分布会整体下移(伪相似剔除)——影响配方推荐排序,**需在方案批准后先跑一次全量相似度分布对比**(旧 vs 新)确认无倒挂再合并;UI 侧无 schema 变化。

---

## P3:claims 验证孤儿线程(chat_claims.py)

**现状(实读)**
`app/services/chat_claims.py` L37-46:
```python
_ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)   # 每次问答新建
_fut = _ex.submit(verify_claims_llm, question, claims, sources)
verified = _fut.result(timeout=12)          # 超时 → 异常 → offline 降级
finally:
    _ex.shutdown(wait=False, cancel_futures=True)   # 杀不掉已运行的网络阻塞线程
```
确认:超时后 LLM 线程继续跑 deepseek(60s idle×2 重试≈2 分钟),每问一池;高频问答 × 慢窗口 → OS 线程积累(不进 FastAPI 线程池,故原审查"接口线程池耗尽"不成立,但积累真实)。

**方案**
1. **模块级共享 executor**:`_CLAIM_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="claim-verify")`,模块首次用时惰性创建;并发超限自然排队(claims 是降级步骤,排队无害)。
2. 超时后 `future.cancel()`(运行中取消无效,但可清队列中未启动任务);共享池线程完成任务后回收,**不再每问新建**。
3. 给 `verify_claims_llm` 内部 LLM 调用传入 `max_retries=1`(现 tenacity 2 次→1 次),把孤儿线程最长寿命 2 分钟压到 ~1 分钟,减少与慢窗口的重叠。
4. 进程退出时 executor 不 shutdown(daemon 化线程),避免 import 周期问题——或放 `atexit`。

**变更文件**:`chat_claims.py`(~20 行)。
**测试**:`tests/test_chat_claims.py`(查现有测试):① mock verify 慢(>12s 挂起)→ 断言 offline 降级 + 线程数不增长(连续 5 次调用后 `threading.active_count()` 增量 <2);② 正常路径不受影响。
**风险**:低。共享池排队语义:若两个请求同时超时,第三个会等——claims 可降级,用户无感;并发上限 2 与 celery `-c 2` 风格一致。

---

## P4:主动学习固定 20% 伪方差(active_learning.py)

**现状(实读)**
`app/services/active_learning.py` L55-59:
```python
# Empirical uncertainty: 20% relative
if objective_metric not in props:
    return 0.0, 0.0
std = abs(mean) * 0.20 + 1e-3      # 固定比例 + 绝对地板
```
确认:训练样本不足(registry 不可用)时所有候选共用同一比例 std;量纲极小属性(mean~1e-4)被 `1e-3` 地板主导,std≫signal → EI/UCB 趋平 → 探索停滞退化为随机(审查称"发散"不成立——有界;停滞成立)。

**方案**
1. std 优先取**同属性实测离散度**:`existing` 中该 `objective_metric` 的记录若 ≥3 条,`std = max(pstdev(实测), abs(mean)*0.05)`,pstdev=0 时(重复实验)下限取属性分辨率(若 knowable)否则 `abs(mean)*0.10`。
2. 不足 3 条 → 保留比例法但地板改为**相对**:`std = max(abs(mean)*0.20, abs(y_best)*0.02)`(y_best 由调用方传入或取 existing 最大——需看 `_ei_acquisition` 调用链,y_best 已在其签名 L62,说明上层有 best——顺调用链取),去掉绝对 `1e-3`。
3. mean 恒 0 且无实测 → 返回 `(0, 0)` 现状保留(调用方已处理 `std<1e-9` → EI=0,L67-68——该分支语义核查后决定是跳过该候选还是给最小探索 std,防探索死区)。

**变更文件**:`active_learning.py`(~25 行)。
**测试**:`tests/test_active_learning.py`(查现有):① 构造 mean=1e-4 属性 → 断言新 std 不出现 1e-3 绝对主导;② 同属性 3 条实测 → 断言用实测 pstdev;③ 回归现有 EI/UCB 行为。
**风险**:低-中。数值行为变化影响 AL 选点顺序——属预期改进;回归测试覆盖确定性分支即可。

---

## 文件变更清单汇总

| 文件 | 改动量 | 批次 |
|---|---|---|
| `backend/app/services/engines/pydoe_engine.py` | +20 | 批 1(P0-P1) |
| `backend/app/services/kg/contradiction.py` | +40 | 批 1 |
| `backend/app/domain/kg_schemas.py`(可能) | +0-5 | 批 1 |
| `backend/tests/test_doe_engines.py` / `test_kg_contradiction.py` | +3/+3 例 | 批 1 |
| `backend/app/services/kg/formulation_similarity.py` | +60 | 批 2(P2,先跑分布对比) |
| `backend/tests/test_kg_similarity.py` | +4 例 | 批 2 |
| `backend/app/services/chat_claims.py` | +20 | 批 2 |
| `backend/tests/test_chat_claims.py` | +2 例 | 批 2 |
| `backend/app/services/active_learning.py` | +25 | 批 2 |
| `backend/tests/test_active_learning.py` | +3 例 | 批 2 |

## 实施顺序

- **批 1(P0+P1)**:改动小、测试确定性强、收益直接(数据正确性)——批准后即可执行,预计半天(含回归)。
- **批 2(P2-P4)**:P2 需先跑旧/新相似度分布对比(脚本出图/分布表)确认无倒挂再合并;P3/P4 独立小改。预计 1-1.5 天。
- 每批完成:单测全绿 → 端到端冒烟 → git 分 commit 提交(main 本地,push 待确认)。

## 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| P0 报错替代静默 → 前端 DOE 生成失败路径未覆盖 | 低 | 查 doe 前端错误提示通用性;失败信息经 notes/异常透传 |
| P1 属性映射枚举不全 → 部分链路误入 unchecked | 低 | 只映射现有 link_type,未知一律 unchecked 不硬猜 |
| P2 相似度分布下移 → 推荐排序变化 | 中 | 合并前跑分布对比 + 抽样人工核验 10 对配方 |
| P3 共享池排队 → claims 延迟 | 低 | claims 可降级,12s 超时已有;并发 2 上限 |
| P4 std 数值变化 → AL 选点顺序变 | 低-中 | 回归测试锁定确定性分支;选点变化属预期改进 |
| 整体回归(chat/kg/doe 测试套件) | — | 每批全量跑相关套件,撞锁再停 |

---

## 执行偏差记录(2026-09-04 实施后)

- **P0**: 按方案落地。未知混料设计名(simplex_centroid)经 native 引擎
  显式报错(doe.py 本就不支持, 无需额外处理)。
- **P1**: 按数据现实修正——图谱测量边对端是 prop:* 属性、文献边对端是
  chem:formula 物质(两个 id 空间), 无法按 target 精确同桶对标; 且
  merge_semantic_link 对同 (src,dst,type) 是覆盖语义, 同属性多测点
  实际不存在。落地为: 属性分桶 + **+1 声称用最差属性 / -1 声称用最好
  属性**(双向防稀释), 归因指向真实属性实体。unchecked 概念未实现
  (需要属性映射, 数据不支持)。
- **P2**: RDKit 指纹路径未落地——formulation_similarity 是纯字符串输入,
  名称→结构仅 molbloom 网络可用(热路径 N×M 不可行), 留 v2。落地为
  别名归一化 + 词法降权 0.15; 且修复了旧实现隐藏归一化 bug(role_weight
  按分值加权 → 词法命中实际拉满相似度到 1.0)。
- **P3/P4**: 按方案落地。
- **遗留**: test_integrations::test_loop_iterate_endpoint 预存失败
  (stash 批 1+2 后复现 17.8s; loop→active_learning_doe 阶段超测试
  10s 轮询窗; 涉及 registry/DataLab 加载链, 待单独深挖)。

*复核基线: 全部论断均来自 2026-09-04 实读代码(行号见各节);5 项被否审查论据记录于文首,未纳入方案。*
