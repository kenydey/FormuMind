# 化学品配方开发领域检索质量提升方案（通用）

> 适用范围：金属表面处理、反腐蚀涂料、脱脂剂、转化膜（含自沉积涂料）
> 对应 `ProductDomain` 四值：`anticorrosion_coating` / `degreaser` / `surface_treatment` / `autodeposition_coating`
> 状态：**纯方案，不改代码**，供评审后决定实施顺序
> 日期：2026-09-10

---

## 0. 结论摘要

检索质量被四件事拖垮，按影响排序：

| # | 根因 | 层级 | 严重度 |
|---|---|---|---|
| 1 | requirement 残留上一个项目的值（碳钢/500h vs 镁合金/720h） | 数据层 | 🔴 高 |
| 2 | 查询扩展只加词、不排词，上位词「防腐蚀涂层」卷进铝/铜/锌/熔盐 | 算法层 | 🟠 高 |
| 3 | `source_policy` 定义了但未接线，arXiv 实际占 55% 且漂移率最高 | 源层 | 🟠 高 |
| 4 | 主题预筛只在「入库后」过滤，检索阶段不拦，193 条里 53.4% 是纯漂移 | 策略层 | 🟡 中 |

**核心判断**：领域分类体系（`DomainSearchProfile`）已经存在且结构正确，问题不在「缺体系」，而在「配置不完整、没接线、缺负向收缩、requirement 污染」。方案基于现成载体做增量，不另起炉灶。

---

## 1. 现状盘点（基于代码事实，2026-09-10 核实）

### 1.1 已具备的资产

| 资产 | 位置 | 状态 |
|---|---|---|
| `ProductDomain` 四值枚举 | `domain/schemas.py:15` | ✅ 已用 |
| `Substrate` 五值枚举（含 magnesium_alloy） | `domain/schemas.py:24` | ✅ 已用 |
| `DomainSearchProfile` 4 套 frozen 配置 | `domain/search_profiles.py:69` | ✅ 结构完整 |
| — arxiv_categories（arXiv 分类过滤） | 同上，已接线 `literature.py:344` | ✅ 生效 |
| — openalex_concept_ids（OpenAlex 概念过滤） | 同上，已接线 `search_providers.py:96` | ✅ 生效 |
| — cpc_prefixes / ipc_codes（专利分类） | 同上，已接线 `query_expander.py:127` | ✅ 生效 |
| — keyword_allow / keyword_deny | 同上，已接线 `topic_gate` / `score_domain_match` | ✅ 生效 |
| — **source_policy（源策略）** | 同上 `search_profiles.py:39` | ❌ **定义未接线** |
| 查询扩展（LLM + CAS 增强 + domain IPC） | `deep_research/query_expander.py` | ✅ 生效 |
| 权威度加成（专利 0.12 / 学术 0.08） | `search_scoring.py:23` | ✅ 生效 |
| 主题预筛（高/低/反向词表 + topic_gate） | `kb_ingest.py:45-170` | ✅ 生效（入库后） |

### 1.2 本轮实测（项目 81c0dfc1，193 条检索）

| 指标 | 值 |
|---|---|
| 检索资料 | 193 条 |
| 纯漂移（0 核心词） | 103 条 = **53.4%** |
| 高相关（≥2 核心词） | 24 条 = 12.4% |
| 源分布 | arXiv 106 (55%) / OpenAlex 34 / Google Patents CN 23 / Scholar 20 / CNIPA 10 |
| requirement vs query | **不一致**：query=镁合金钝化/720h，requirement=防腐蚀环氧底漆/碳钢/500h |

---

## 2. 五层方案

### L0 数据层 —— requirement ↔ query 一致性（最优先，零成本）

**问题**：`requirement` 是结构化检索上下文（domain/substrate/salt_spray_hours 等），`query` 是自然语言检索词。两者不一致时，查询扩展、后续推荐、DOE、配方生成全部被污染。本轮就是典型：新建项目时 `requirement` 残留了上一个项目的值。

**方案**（三选一或叠加）：

1. **新建项目强制对齐**：项目创建时，若用户填了 query，则从 query 解析出 domain/substrate/objective 并回填 requirement；反之从 requirement 生成 query 摘要。两者必须同源，禁止一个手填一个残留。
2. **requirement 版本化 + 一致性校验**：每次检索前校验 `requirement.domain` 与 query 语义是否一致（用现有 `score_domain_match` 判 query 归属哪个 domain），不一致时**阻断检索并提示**，而非静默继续。
3. **Substrate 硬约束下钻**：检索扩展时把 `requirement.substrate`（如 magnesium_alloy）作为必带锚词注入所有查询（`build_*_query` 的 topic 参数已预留此位置），使「镁合金」成为强约束而非可漂移词。

**落地载体**：`project_workspace.py`（创建/持久化）、`query_expander.py:prepare_search_queries`（已接收 `domain`，缺 `substrate`）。
**预期收益**：漂移率 ↓30%+，消除「上下文污染」这一类问题。
**优先级**：P0，成本最低，收益最大。

---

### L1 算法层 —— 查询扩展从「只加词」到「加词 + 排词 + 定界」

**问题**：`query_expander.py` 的 `_EXPAND_PROMPT` 只让 LLM 产出正向扩展词（chinese_keywords / english_synonyms / ipc_cpc），**没有负向词表**。上位词「防腐蚀涂层」天然会把铝/铜/锌/熔盐/核材料全卷进来——这正是 53.4% 漂移的来源。

**方案**：

1. **负向词表（deny terms）纳入扩展输出**：扩展 prompt 增加 `negative_terms` 字段，LLM 依据 domain 产出应排除的词（如 anticorrosion_coating 排除 uranium / copper / rebar / hot-dip galvanizing / nuclear）。
2. **domain 级负向词固化**：在 `DomainSearchProfile.keyword_deny` 基础上补充**检索期负向词**（现有 keyword_deny 只在入库 gate 用，检索阶段未用）。为每个 domain 维护一张 `search_deny` 词表（含中文+英文+词干）。
3. **定界（scoping）**：扩展词按「工艺词 / 功能词 / 基材词」分层，基材词（镁合金）设为 hard 约束（必须命中），工艺词（钝化/转化膜）设为 soft 加权——避免「涂层」这类宽泛词把检索面撑大。

**落地载体**：`query_expander.py`（扩展 prompt + SearchQueries 增加 negative/scoping 字段）、`search_profiles.py`（每 domain 增加 search_deny + 词分层）。
**预期收益**：漂移率 53% → <15%，且不牺牲召回（负向只排「已知无关」，不动「边界相关」）。
**优先级**：P1，与 L2 并列。

---

### L2 源层 —— source_policy 接线 + 源权重按 domain 校准

**问题**：`DomainSearchProfile.source_policy` 字段已定义（arxiv/openalex/epo/google_patents=primary，scholar/surechembl=support），**但没有任何检索执行器消费它**——是死配置。结果 arXiv 实际占 55%，而它是漂移率最高的源。

**方案**：

1. **接线 source_policy**：检索编排器（federated_search / search.py）按 profile 的 source_policy 决定每个源的**查询配额与结果上限**——`primary` 源拿全配额，`support` 源降配额（如降 50%），`off` 源跳过。
2. **按 domain 差异化源策略**（覆盖四个 domain 的通用规律）：
   - **专利密集型 domain**（anticorrosion_coating / surface_treatment / autodeposition_coating）：提升 Google Patents CN / CNIPA / EPO / SureChEMBL 权重，arXiv 降为 support 或按 arxiv_categories 严格限定。
   - **配方化学 domain**（degreaser）：OpenAlex 概念（cleaning/degreasing/surfactant）+ 专利并重，arXiv 基本不相关。
3. **源可信度评分**（中期）：按「全文获取率 × 主题命中率 × 噪声率」给每个源打分，低分源降权或剔除——呼应你既有的「无法下载全文的源对 FormuMind 无价值」原则。

**落地载体**：`federated_search.py`、`search_profiles.py:source_policy`（字段已就绪，只差消费端）。
**预期收益**：arXiv 占比 55% → 30% 以下，专利源占比提升，漂移率进一步下降。
**优先级**：P1。

---

### L3 策略层 —— 全链路过滤策略

**问题**：过滤动作分散且滞后——「无全文→不进列表」策略未落地，检索结果没有相关度分层，入库后没有反向审计。

**方案**（四个策略，可独立实施）：

1. **无全文即不进列表**（你已定方向，尚未落地）：全文获取失败（403/404/OA 缺失）的源，从左栏「已加载资料」列表直接移除，不占位、不误导。**通用规则**：`fetchable=false` → 不进列表。
2. **检索结果相关度分层**：左栏按 domain_match（strong/weak/none）分三档，默认折叠 `none` 档，用户可展开。基于现有 `score_domain_match` 即可。
3. **入库后反向审计**：自动标记两类文档——「入库但 0 chunks」「入库但相关度=none」，进一个待清理清单，定期人工复核。
4. **检索可解释性**：每条资料标注命中来源（哪次扩展词命中 / 哪个 taxonomy 命中），用户可追溯漂移来自哪个扩展词，反哺 L1 的负向词表。

**落地载体**：`search.py`（结果返回前过滤）、前端左栏（分层展示）、`kb_ingest.py`（入库后审计 hook）。
**预期收益**：列表干净度、人工筛选成本 ↓80%，形成「漂移可追溯→负向词表迭代」闭环。
**优先级**：P2（策略 1 可提至 P1）。

---

### L4 主题预筛 —— 从「入库后一道闸」到「三级门控」

**问题**：现有 `topic_gate` 只在**入库前**过滤（`select_ingest_targets`），检索阶段不拦。所以 193 条里 103 条漂移照样被检索、下载、打分、展示，只是最后不入库——**浪费了检索与下载的算力和配额，还污染了左栏展示**。

**方案**（三级门控，前移为主）：

| 级 | 位置 | 动作 | 目的 |
|---|---|---|---|
| 门控 I（检索时） | `search.py` / 各 source 检索后 | 用 `score_domain_match` 判 strong/weak/none，**none 直接丢弃不进结果集**（或标记后仅计数不展示） | 省下载配额、省展示位 |
| 门控 II（入库前） | `select_ingest_targets`（现有） | 现有 topic_gate 保留，**把 domain 的 keyword_deny 词表并入反向硬拦词**（现有 _TOPIC_BLOCK 是全局通用词，未与 domain keyword_deny 合并） | 拦截领域外 |
| 门控 III（入库后） | 入库完成 hook | 反向审计（同 L3 策略 3） | 兜底 + 数据可观测 |

**关键改进点**：
1. 门控前移到检索时，避免「检索到 103 条垃圾 → 全部下载 → 入库前才拦」的浪费。
2. 现有 `_TOPIC_BLOCK` 是全局通用硬拦词，应**与 domain profile 的 keyword_deny 合并**，让每个 domain 有专属的领域外信号（如 degreaser 拦 laundry/dishwash/cosmetic，anticorrosion 拦 battery/photovoltaic）。
3. `topic_gate` 对专利已不再是盲免（需 CPC/lexical 命中），这个方向正确，保持。

**落地载体**：`search.py`（门控 I）、`kb_ingest.py:topic_gate`（门控 II 词表合并）、`domain_tagging.py`。
**预期收益**：检索结果即过滤，下载量 ↓50%+，左栏噪声消除。
**优先级**：P1（门控 I）+ P2（词表合并）。

---

## 3. 统一载体：DomainSearchProfile 增强

五层方案里，L1/L2/L4 都落在同一个载体上——`DomainSearchProfile`。建议把增强项统一挂到它上面，保持「一个 domain 一套 frozen 配置」的既有架构：

```python
@dataclass(frozen=True)
class DomainSearchProfile:
    # ...现有字段...
    search_deny: tuple[str, ...]        # 新增：检索期负向词（L1）
    substrate_anchor: tuple[str, ...]   # 新增：基材强约束词（L0，从 Substrate 枚举映射）
    source_quota: Mapping[str, float]   # 新增：源配额权重（L2，替换/细化 source_policy）
```

这样每个 domain 的检索行为由一张配置表完全决定，新增 domain（未来第五、六个产品线）只需加一个 profile，无需改检索代码——**这是通用化的核心**。

---

## 4. 分阶段实施路线

| 阶段 | 内容 | 涉及层 | 风险 | 回滚方式 |
|---|---|---|---|---|
| 第一阶段（1-2 天） | requirement 一致性校验 + Substrate 强约束下钻 + 无全文不进列表 | L0 + L3-1 | 低 | feature flag 关闭 |
| 第二阶段（2-4 天） | 检索期负向词表 + 门控 I 前移 | L1 + L4 | 中（可能漏召回） | flag 关闭 + 负向词表可清空 |
| 第三阶段（3-5 天） | source_policy 接线 + 源配额按 domain 校准 | L2 | 中（源覆盖变化） | 权重可回默认 |
| 第四阶段（迭代） | 相关度分层 UI + 入库后审计 + 检索可解释性 | L3 | 低 | UI 回退 |

每阶段独立可上线、独立可回滚，不要求一次性做完。

---

## 5. 风险与权衡

| 风险 | 说明 | 缓解 |
|---|---|---|
| 漏召回（误杀边界相关） | 负向词/门控过严可能漏掉真正相关但表述冷门的文献 | 门控用「soft 降权 + hard 排除」分级，hard 排除词只放「已知无关」；保留 `skip_topic_filter` 逃生舱 |
| 源覆盖变化影响专利检索 | 降 arXiv 权重可能漏掉 arXiv 上的前沿防腐蚀研究 | arXiv 按 arxiv_categories 严格限定而非一刀切 off |
| 负向词表维护成本 | 词表需持续迭代 | 结合 L3-4「检索可解释性」形成「漂移可追溯→词表迭代」闭环，把维护变成数据驱动 |
| 与现有 SureChEMBL/专利集成冲突 | 源策略调整可能影响已上线的专利检索 | 专利源（patents）默认保持 primary 不动，只调学术源 |

---

## 6. 建议的决策点（需你拍板）

1. **门控前移（L4 门控 I）是否接受「宁缺毋滥」**：检索阶段直接丢弃 none 档，会牺牲极少数「表述冷门但相关」的文献，换来 50% 下载量与噪声的节省。这是本次方案里唯一有「质量 vs 召回」权衡的点。
2. **source_policy 的 arXiv 定位**：是「降为 support」还是「按分类严格限定保留 primary」？（我建议后者，前沿防腐研究仍有 arXiv 价值）
3. **负向词表由谁维护**：LLM 自动生成 + 人工复核，还是纯人工？这决定 L1 的实施成本。

---

*本方案基于 2026-09-10 对 `search_profiles.py` / `query_expander.py` / `kb_ingest.py` / `search_scoring.py` / `domain_tagging.py` 的代码核实，未改动任何代码。*
