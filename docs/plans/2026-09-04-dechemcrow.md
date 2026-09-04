# FormuMind 去 ChemCrow 化实施计划

- **日期**: 2026-09-04
- **状态**: 待评审
- **目标**: 从 FormuMind 彻底移除 ChemCrow(依赖 + 代码 + venv 补丁),现有全部功能行为不受影响;补齐「合成可行性初判」能力缺口
- **前置结论**(已核查): ChemCrow 停维护(最新 0.3.24 仍 pin langchain≤0.0.275);FormuMind 实际只用其 5 个轻量查询工具;替代方案候选(ChemToolAgent/Coscientist)均不适用(详见 2026-09-04 评估);native 实现已覆盖 90% 功能

---

## 一、架构图(现状 → 目标)

### 现状
```
                  ┌─────────────────────────────────────────────┐
                  │           chemtools.py (网关)               │
  上游调用方       │  chemcrow_available() ? → chemcrow.tools     │
 formulation_gate │  ├ Query2SMILES  ├ Query2CAS                 │
 chemical_lookup  │  ├ FuncGroups    ├ PatentCheck               │
 query_expander   │  └ ExplosiveCheck                            │
 ip_analysis      │  ↓ 不可用/缺库时 fallback                    │
 llm.py (agent)   │  └ PubChem / RDKit / molbloom(native 已有)    │
 literature.py    │                                              │
 (chemweb检索)    │  + chemcrow ReAct agent 问答(llm.py)         │
                  └─────────────────────────────────────────────┘
       依赖: chemcrow 0.3.7(venv, 24 文件被打补丁) → langchain 0.x
       链: langchain(1.3.14, RAGatouille 共享) ← chemcrow ← rmrkl
```

### 目标
```
                  ┌─────────────────────────────────────────────┐
  上游调用方       │        chemtools.py (native-only)          │
 formulation_gate │  name_to_smiles → PubChem REST             │
 chemical_lookup  │  name_to_cas    → PubChem REST             │
 query_expander   │  func_groups    → RDKit(SMARTS 本地)        │
 ip_analysis      │  patent_check   → molbloom(本地 Bloom)      │
 literature.py    │  explosive_check→ PubChem GHS + 本地清单     │
 (chemweb→serpapi)│  synthetic_accessibility → RDKit sascorer  │  ← 新增
                  │        (全部确定性, 零第三方 agent 依赖)      │
                  └─────────────────────────────────────────────┘
       依赖: rdkit ✓ / molbloom ✓ / httpx ✓(均已装, 不动)
       chemcrow + 补丁 + ReAct agent 路由: 全部删除
```

## 二、真调用点与改动清单(按文件)

侦察结果:25 个文件含 "chemcrow" 字样,**真调用 8 个**,其余为证据源标签/健康探测/依赖声明/测试。

### A 类: 核心改造(5 文件)

| 文件 | 现状 | 改动 |
|---|---|---|
| `backend/app/services/chemtools.py` | chemcrow 优先分支 ×5 + `_chemcrow_tool` + `chemcrow_available` + 合成可行性缺失 | 删 chemcrow 分支与函数;native 逻辑提升为主路径;新增 `synthetic_accessibility(smiles)`(RDKit sascorer, 见 §四) |
| `backend/app/services/llm.py` | `_chemcrow_answer`(1546) + `_chemcrow_llm_ready`(1527) + `_chemcrow_available`(1523) + 化学问题路由(1635) | 删 4 函数;化学类问题路由改走现有 `answer_question`(deepseek 直答, 不引 agent) |
| `backend/app/services/literature.py` | `search_chemcrow_web`(845) 调 chemcrow WebSearch(=SerpAPI 封装) | 改为直调现有 SerpAPI 检索函数;`split_chemcrow_answer` 保留(纯文本 DOI 解析, 无依赖)但改名 `split_lit_answer`(兼容别名) |
| `backend/app/services/chemical_lookup.py` | Tier 4 chemcrow 查询(152) | 删 Tier 4;PubChem Tier 已覆盖同能力 |
| `backend/app/domain/formulation_gate.py` | `_resolve_fields` 依赖网关(56) | 判断条件改 `gateway_enabled() and pubchem_available()` 语义(native);P1 网络兜底逻辑保留不动 |

### B 类: 网关条件改写(3 文件, 均为 `chemtools.chemcrow_available()` → native 判定)

| 文件 | 改动 |
|---|---|
| `backend/app/services/query_expander.py`(87) | 条件改 `gateway_enabled()`(内部已含 rdkit/pubchem 可用性) |
| `backend/app/services/ip_analysis.py`(218) | 同上 |
| `backend/app/pipeline/workflow.py`(86/100 注释) | 注释更新(enrich/预筛说明去 chemcrow 字样);`_evidence_matches_type` 源标签匹配保留(兼容历史) |

### C 类: 声明/探测/标签清理(6 文件)

| 文件 | 改动 |
|---|---|
| `backend/app/config.py`(199-200) | 删 `use_chemcrow` 字段(确认无其他引用后) |
| `backend/app/services/env_flags.py`(186-191) | 删 `use_chemcrow`/`chemtools_enabled` 两个 EnvFlag(如 UI 有引用则保留 `chemtools_enabled` 作为总开关) |
| `backend/app/services/dependencies.py`(54-55) | 删 chemcrow `Dependency` 条目 |
| `backend/app/main.py`(439-442) | health 探测 chemcrow 键改 `None`(与 paperqa 同列, 不再探测) |
| `backend/pyproject.toml`(54) | 删 `chemcrow>=0.3.7` 依赖行 |
| `scripts/dev/patch_chemcrow_langchain1.py` | 整个删除(补丁使命结束) |

### D 类: 历史证据源标签(4 文件 — **保留不改**, 兼容既有数据)

`colbert_store.py:97` / `content_filter.py:157` / `deep_research/models.py:23` / `workflow.py:154-157`
理由: `chemcrow-web`/`chemcrow-lit`/`ChemCrow-Web` 等出现在**已入库文档与证据行**的 source 字段;检索过滤/去重/路由依赖这些字面量。删除会破坏历史数据行为。新代码不再产生这些标签(§A 改造后)。

### E 类: 测试改造(7 文件)

| 文件 | 改动 |
|---|---|
| `tests/test_chemtools.py` | 删 `_install_fake_chemcrow` 机制与 `*_without_chemcrow` 环境依赖测试(上轮加的 `chemcrow_available→False` monkeypatch 全删);保留 native 行为测试;新增 sascorer 测试 |
| `tests/test_chemcrow_recommend.py` | 改名 `test_chem_gate_recommend.py`;fake chemcrow → 直测 native 路径(PubChem/RDKit mock) |
| `tests/test_chemcrow_retrieval.py` | 同上改名;`split_lit_answer` 纯文本测试保留 |
| `tests/test_chemcrow_doe_data.py` | fake 机制删除, DOE 数据测试改 native |
| `tests/test_dependencies.py`(46) | 断言集合去 `"chemcrow"` |
| `tests/test_integrations.py`(107) | `_chemcrow_available` 探测测试 → 删或改 `llm` 无该函数断言 |
| `tests/test_v03.py`(284-325) | 源列表/health 断言去 chemcrow 键 |

### F 类: venv 清理(不重建 venv)

- `pip uninstall -y --no-deps chemcrow` + 删除补丁残留(chemcrow 目录/ dist-info 已随卸载清除)
- **保留**: langchain(1.3.14 — RAGatouille Required-by)、rdkit、molbloom、httpx
- **保守不卸**: streamlit/paper-qa/synspace/rxn4chemistry/rmrkl/google-search-results/nest_asyncio/tiktoken(chemcrow 独有但卸载有连锁风险;列为可选激进清理, 单独验证)
- 验证: `python -c "import chemcrow"` → ModuleNotFoundError;全量测试绿

## 三、功能等价映射(现有功能不受影响 — 逐项)

| 现有能力 | 现在走 | 之后走 | 行为差异 |
|---|---|---|---|
| 材料名→SMILES | chemcrow Query2SMILES → PubChem fallback | PubChem REST 直连 | 无(同数据源;防滥用 guard 保留) |
| 材料名→CAS | chemcrow Query2CAS → PubChem fallback | PubChem REST 直连 | 无 |
| 功能团识别 | chemcrow FuncGroups(RDKit SMARTS 封装) | RDKit SMARTS 本地(chemtools 既有实现) | 无(chemcrow 底层就是 RDKit) |
| 专利预筛 | chemcrow PatentCheck → molbloom fallback | molbloom 直连 | 无 |
| 爆炸物/GHS | chemcrow ExplosiveCheck → PubChem fallback | PubChem GHS + 本地清单 | 无 |
| 化学问题问答(ReAct) | use_chemcrow → chemcrow agent(GPT 系) | deepseek `answer_question` 直答 | 换模型(现唯一 LLM 即 deepseek, 本就走不通 GPT 系——**修复了隐性死路**) |
| 化学优化 web 检索 | chemcrow WebSearch(SerpAPI) | 现有 SerpAPI 检索函数直调 | 无(同 API) |
| 长尾材料名兜底 | P1 Tavily 检索补 CAS | 不变 | 无 |
| **合成可行性初判** | **缺失**(chemcrow 0.3.7 从未提供) | `synthetic_accessibility()` 新增 | **能力新增**(见 §四) |

## 四、合成可行性初判替代方案

**选型: RDKit 内置 SA score(sascorer, Ertl & Schuffenhauer 2009)**
- 理由: rdkit 已装零新增依赖;纯本地 CPU 秒级;行业标准合成可及性打分(1=易合成 → 10=极难);MIT 兼容
- 被否候选: SCScore(需模型权重 + 独立包, 增量收益小)/ ASKCOS(自托管服务, 架构过重)/ ChemMCP Uni-Mol 系(需 GPU, VPS 不可行)
- 接口设计(新增至 chemtools.py):
  ```
  synthetic_accessibility(smiles: str) -> dict
    → {"sa_score": 2.3, "tier": "easy", "note": "1-10, 越低越易合成"}
  tier 映射: ≤3 easy / ≤6 moderate / ≤8 hard / >8 very_hard
  ```
- 无 SMILES / 解析失败 → `{"sa_score": None, "tier": "unknown", "note": ...}`(不崩, 中性降级, 与现网关约定一致)
- 挂接点(初版只暴露工具, 不进自动管线): `GET /api/chemical/lookup` 响应附 `synthetic_accessibility` 字段;配方推荐 IP 预筛结果可后续选用
- 局限说明(写进 docstring): SA score 面向有机小分子;配方中的聚合物/无机盐类(无 SMILES 或非小分子)返回 unknown——**金属表面处理场景多数材料属此类, 该字段是有则加分、无则中性**——不改变任何现有推荐逻辑

## 五、实施步骤时间表

| # | 步骤 | 内容 | 预计 | 验证门 |
|---|---|---|---|---|
| 1 | chemtools.py 重构 | 删 chemcrow 分支/函数;native 主路径;新增 synthetic_accessibility | 2h | 单测(改造后 test_chemtools)绿 |
| 2 | 调用方改造 | formulation_gate / chemical_lookup / query_expander / ip_analysis / workflow 注释 | 1h | 相关测试绿 |
| 3 | llm.py agent 路由删除 | 删 4 函数;路由改 answer_question;验证化学问答直答 | 1h | chat 化学问题实测(中文) |
| 4 | literature.py | search_chemcrow_web → SerpAPI 直调;split 改名+兼容别名 | 1h | 检索单测绿 |
| 5 | 声明/探测清理 | config / env_flags / dependencies / main / pyproject / patch 脚本删除 | 0.5h | import 全绿(启动无缺失引用) |
| 6 | 测试改造 | E 类 7 文件(fake 机制删除、改名、断言更新)+ 新增 sascorer 测试 | 2h | 全量 pytest 绿(1762+ 项) |
| 7 | venv 清理 | pip uninstall chemcrow(保守级);验证 import 失败 | 0.5h | `import chemcrow` 抛错;全量再绿 |
| 8 | 端到端验证 | 重启 backend+worker;实测: 材料 SMILES 查询 / 功能团 / IP 预筛 / 化学问答 / 配方推荐全链路 | 1h | 浏览器/API 实测通过(非仅单测) |
| 9 | 文档同步 | formumind-development skill 更新(移除 chemcrow 移植段落);git 提交(分 fix/refactor/test 逻辑 commit) | 0.5h | push main |

总计 ≈ **9.5 小时**(含验证缓冲)

## 六、风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 隐性 chemcrow 引用遗漏(非 grep 可见, 如动态 import) | 低 | 中 | 改造后全量测试 + 全代码 grep 复核 + 启动日志监控 |
| literature 源标签清理破坏历史数据检索 | 中 | 中 | **D 类标签全部保留不改**(已在 §二 D 明确) |
| `use_chemcrow`/`chemtools_enabled` 有前端 UI 引用, 删除后设置页报错 | 中 | 低 | 步骤 5 前 grep 前端 env_flags 渲染;有引用则保留 flag 壳(仅去后端逻辑) |
| llm.py 化学路由删除后化学问答质量下降(deepseek 无工具检索) | 中 | 中 | 路由改走现有 RAG 问答(有资料检索);实测对比;质量不达标则保留 `answer_question` + 化学提示词增强 |
| venv 卸载连带破坏共享包 | 低 | 高 | **--no-deps + 保守清单**;卸载后立即 import rdkit/molbloom/langchain 验证 |
| 测试改造中误删行为覆盖(原 chemcrow 路径有独特解析逻辑) | 低 | 中 | 重构前先记录各工具 native fallback 测试基线;重构只删分支不删解析函数 |
| 全量测试网络挂起(3.170.x 坏节点, 上轮已现) | 中 | 低 | 分段跑;网络测试与本地测试分开验证 |

## 七、不做的事(范围边界)

- 不引入 ChemToolAgent / Coscientist / ChemMCP(评估见前文, 功能错位 + 商用许可/GPU 限制)
- 不改证据源历史标签(§二 D)
- 不重建 venv / 不升级 rdkit / 不动 langchain 1.3.14(RAGatouille 依赖)
- 不加 UI 新控件(合成可行性只作为查询响应字段, 前端后续按需展示)
