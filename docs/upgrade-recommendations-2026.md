# FormuMind 工业级升级建议（开源路线）

> 编写日期：2026-09-26。目标：在尽量使用开源方案的前提下，提升 FormuMind 的**检索资料能力**、**RAG 与 LLM Wiki 知识库构建能力**、**推荐配方能力**，并补齐工业级工程短板。
> 调研依据：2026-09-26 针对 2026 年开源生态的专项调研（检索 / RAG / 配方优化 / 可观测性四个方向，全部为 index 级证据，采购前请逐项核对各项目的 LICENSE 文件）。

## 0. 总览：优先级与路线图

| 阶段 | 内容 | 预期收益 |
|---|---|---|
| **Phase 1（P0，高 ROI、低风险）** | EPO OPS + OpenAlex/Unpaywall 检索连接器；Docling 定为解析主链路；Qwen3-Embedding 替换 TF-IDF；DeepEval 接入 CI 做回归门禁 | 检索覆盖从"抓落地页"升级为官方 API；中英解析与检索质量跃升；RAG 质量可度量 |
| **Phase 2（P1，中等投入）** | Qdrant 向量库替代 FAISS 文件索引；reranker（Qwen3-Reranker）；BayBE 替换自研 numpy UCB 优化器；MLflow + DVC | 检索规模化（100k–1M chunk）、配方优化从"能用"到"工业级 DoE" |
| **Phase 3（P2，按需）** | Google Patents BigQuery 批量分析；SureChEMBL 离线化学专利挖掘；Langfuse/Opik 可观测 | 专利全景分析、化学结构级专利挖掘、LLM 行为可追踪 |

**明确不上关键路径的**：生成式配方设计（GFlowNet/扩散模型，2026 年仍是 research-grade）；STORM 官方包（维护停滞，只取方法论）；任何 GPL/AGPL/SSPL 组件（见 §5 红线）。

---

## 1. 检索资料能力

### 1.1 现状
当前检索依赖 Google Patents 落地页、arXiv、Semantic Scholar、DuckDuckGo，属于"网页抓取 + 通用学术 API"模式：专利无官方结构化接口（族、法律状态、全文不可靠），中文专利/文献覆盖弱。

### 1.2 建议

**专利（按优先级）：**
1. **EPO OPS v3.2**（免费配额约 4GB/周）——全球专利检索的官方 API：EP/WO 全文、INPADOC 同族、法律事件，多局数据交换。替代现在 Google Patents 落地页抓取的主链路。注意配额计量、需联网。
2. **Google Patents Public Data on BigQuery**（CC BY 4.0，1.66 亿+ 公开，覆盖 17+ 国家）——做批量专利分析/全景（landscape），SQL 直接查全球著录 + 美国全文。免费层每月 1TB，需 GCP 项目；离线场景可导出。
3. **SureChEMBL bulk**（CC BY-SA 3.0，3100 万+ 专利化合物，两周更新）——化学专利结构挖掘：EP/WO/US 全文、JP 著录+英文摘要、CN 英文翻译。注意 **share-alike 数据条款需法务确认**；只有 bulk Parquet，无公开 REST API，离线用 DuckDB/FPSim2 建索引。
4. **USPTO Open Data Portal (ODP) bulk**——PatentsView 旧 API 已下线（2025-05），美国专利走 ODP 批量数据（`pvgpatdis` 摘要/CPC/消歧实体，`pvgpattxt` 权利要求/说明书全文）。

**文献：**
- **OpenAlex**（免费、计量制，2.5 亿+ works）——替代/补充 Semantic Scholar 的默认元数据/引用/OA 发现层。
- **Unpaywall**（免费，需传 email 参数）——DOI → 合法 OA PDF 的最佳路由。
- **CORE v3**（免费 key）——OA 全文聚合，拿全文语料。
- **明确缺口**：CNKI 仍付费墙 + 反爬，**不存在开源的中文学术全文路线**，中文文献覆盖是已知短板，不要对外承诺。

**工程建议**：把检索层抽象为 connector 接口（FormuMind 已有多源模式），新增 `ops_client`、`openalex_client`、`unpaywall_client`，现有抓取链路保留为 fallback；检索结果统一落"证据对象"（source ID + 页码锚点），供 RAG/Wiki 复用。

### 1.3 成本与风险
- EPO OPS/OpenAlex/Unpaywall 均为免费配额制，成本主要是开发 connector；BigQuery 需 GCP 项目（免费层够用）。
- SureChEMBL 的 CC BY-SA share-alike 条款：若其数据衍生进入商业产品需法务评审。

---

## 2. RAG 与 LLM Wiki 知识库能力

### 2.1 现状
BM25 + FAISS 混合检索、TF-IDF fallback、可选 ColBERT；解析链 pymupdf4llm → docling → marker → MinerU 多路并存；Wiki 走 STORM 报告模式；已有 `golden_eval_dataset.py` 但未形成 CI 门禁。

### 2.2 建议（按数据流向）

**解析（ingest）：**
- **Docling（MIT）定为默认解析 backbone**：版式分析 + TableFormer（表格单元准确率 97.9%），PDF/DOCX/PPTX/XLSX/HTML/图片全本地 CPU 可跑，有 LangChain/LlamaIndex 适配器。中文支持走 Granite-Docling（Apache-2.0，"早期"中文支持——需在自有文档上验证）。
- OCR 补充：**PaddleOCR PP-OCRv5 / PP-StructureV3（Apache-2.0）**，中文 OCR 精度/参数比最优（~100M 参数），与 Docling 搭配。
- **避开**：Marker/Surya（GPL-3.0）、MinerU（AGPL，待核实——无论如何 AGPL 不进闭源产品）。

**Embedding：**
- **Qwen3-Embedding-0.6B（Apache-2.0）**：中英科学文本质量/CPU 开销的最佳折中（32K 上下文、Matryoshka 32–1024 维、有 GGUF 量化）。替换 TF-IDF fallback，成为默认 dense 向量。
- 备选 **BGE-M3（MIT）**：dense+sparse+ColBERT 三合一，若想用它的统一稀疏/晚交互输出可考虑。

**向量库：**
- **Qdrant（Apache-2.0）**：默认选择。单二进制/Docker、丰富过滤、sparse+dense 原生、量化，替代 FAISS 文件索引，支撑 100k–1M chunk 规模化。
- 若 PostgreSQL 已是系统主库：**pgvector**（少一个服务，Postgres FTS 做混合）。
- 纯嵌入式离线发行：**LanceDB（Apache-2.0）**。
- **避开**：Elasticsearch（Elastic/SSPL 许可证陷阱）、Pinecone（云端专有，与离线目标冲突）。

**混合检索 + 重排：**
- 默认管线：**dense（Qwen3）+ BM25，RRF 融合 → rerank top 20–50**。
- Reranker：**Qwen3-Reranker-0.6B（Apache-2.0）**（中英科学文本语言适配最好）；CPU 基线用 **BGE reranker base**。只重排 top 20–50 + 量化 + 缓存，控制 CPU 延迟。
- SPLADE：等中英化学评测证明有提升再加；有 2026 年实验表明把扩展 token 粗暴塞进 BM25 反而比纯 BM25 差。

**LLM Wiki（STORM 式生成）：**
- **STORM 官方包只做方法论参考**（stanford-oval/storm，MIT；2024-09 后进入研究性维护，2026 年无活跃维护证据），**不要作为生产框架引入**。
- 生产模式自研：claim 级证据对象 → 稳定 source ID + 页码锚点 → 大纲驱动生成 → 生成后引用蕴含校验（unsupported claim 拒绝）。用 RAGAS faithfulness / claim 分解检查做校验层。

**评估（工业级关键）：**
- **DeepEval（Apache-2.0，pytest 原生，50+ 指标）**：接入 CI 做 RAG 回归门禁——这是从"demo"到"工业级"的分水岭。
- **RAGAS（Apache-2.0）**：做探索性分析与看板（faithfulness / answer relevance / context precision-recall）；注意 LLM-as-judge 指标需用人标数据校准阈值，默认阈值 2026 年 head-to-head 表现不佳。
- LangChain 旧 evaluator 已冻结，不作为主评估层。

### 2.3 成本与风险
- Qwen3-Embedding-0.6B / reranker 在 CPU 上可行但需量化 + 基准测试（在自有化学语料上跑一遍，不要信社区分数）。
- Qdrant 引入一个新服务；若团队已用 Postgres，pgvector 运维成本更低。
- Granite-Docling 的中文支持标为"早期"，需实测中文专利 PDF（含公式、表格）。

---

## 3. 推荐配方能力

### 3.1 现状
经验代理模型 + sklearn（RandomForest）训练路径 + numpy ridge；优化器是自研 numpy UCB，Optuna 可选；chemcrow 可选（但与 openai>=1.30 冲突，已踩过坑）。

### 3.2 建议

**DoE / 贝叶斯优化（核心升级）：**
1. **BayBE（Apache-2.0，Merck KGaA EMD，非常活跃）**——配方 campaign 的首选引擎：连续+离散混合空间、离散/连续约束（含基数约束）、Pareto + desirability 多目标、内置化学编码、迁移学习、可序列化。**替换自研 numpy UCB**。注意小版本间有 breaking change，上生产要 pin 版本。
2. **BoFire（BSD-3，BayBE 团队分拆）**——最接近的备选，按 API 契合度二选一。
3. **BoTorch/Ax（MIT）**——算法底座：自定义采集函数（如 qNEHVI）、混合空间优化；Ax 做人机协同实验平台层。
4. **Optuna v5（MIT，2026-09-07 发布）**——通用黑盒优化基线；v5 新增约束优化 API、默认采样器多年首次变更，拉近了与约束 BO 的差距。

**多目标策略（实用规则）：**
- 昂贵的湿实验 → **qNEHVI**（贝叶斯期望超体积提升，样本高效、可 batch，BoTorch/Ax 原生）。
- 便宜的 in-silico 筛选 → **NSGA-II**（演化算法，多目标、大候选多样性，BoTorch 有 pymoo 工具）。
- 需要给业务方单一排序 → desirability 标量化（BayBE/BoFire 内置）。

**经典 DoE 别丢**：**pydoe（BSD-3）** 的 mixture designs（混料设计）是配方领域的刚需基线，只管生成设计、不管拟合。

**分子/QSPR 建模（CPU 可行）：**
- **RDKit（BSD）+ sklearn / LightGBM**：CPU 上 QSPR 的主力组合（指纹、描述符 + 经典模型），已在科学栈内，继续用。
- **DeepChem（MIT）**：统一分子 ML（featurizer、MoleculeNet、GNN）；CPU 跑经典/指纹模型没问题，GNN/Transformer 训练才要 GPU。注意 PyPI 2.8.0 限制 Python <3.12（社区注记）。
- **MoLFormer-XL（Apache-2.0，代码+权重已验证）**：SMILES 化学语言模型，**只做特征提取/微调，不可做分子生成**（官方明确）。
- **ChemBERTa-2 权重许可证缺失**（HF 热门 checkpoint 无权重许可声明）——商用前必须确认，暂时不用。

**生成式/逆向设计**：RxnFlow（MIT）、CGFlow（MIT）等 2026 年仍是 research-grade，**不上关键路径**，列入观察清单；任何采用前先与"简单 BO + 代理模型"做基准对比。

### 3.3 成本与风险
- BayBE/BoTorch 引入 PyTorch/GPyTorch 运行时，比 numpy 重，但这是工业级 BO 的必要代价。
- chemcrow 与 openai>=1.30 的冲突（已验证）若保留 chemcrow 需做依赖隔离或二选一。

---

## 4. 工业级横切能力

| 方向 | 推荐 | 说明 |
|---|---|---|
| LLM 可观测 | **Langfuse**（MIT core，open-core；2026-01 被 ClickHouse 收购，暂无改许可证承诺）或 **Opik**（Apache-2.0，全许可） | 追踪 RAG/推荐链路的 prompt、检索、引用；Langfuse v3 自托管较重（web+worker+Postgres+ClickHouse+Redis+S3），轻量可选 Opik 或 OTel 原生的 OpenLLMetry |
| 实验/模型管理 | **MLflow 3.0**（Apache-2.0，GenAI tracing） | 配方实验跟踪 + 模型 registry，替代散落的文件 |
| 数据版本 | **DVC**（Apache-2.0） | Git 式数据/管线版本，无需服务端；lakeFS 社区版 2026 年转 BSL，**不用** |
| 评估门禁 | DeepEval 进 CI（见 §2.2） | 每次改检索/模型，先过 golden 集 |

---

## 5. 许可证红线（采用前必查 LICENSE 文件）

| 项目 | 问题 |
|---|---|
| Marker / Surya | GPL-3.0 |
| MinerU | AGPL（待核实；无论如何不进闭源产品） |
| Jina embeddings v3 | CC BY-NC（非商业） |
| Arize Phoenix（server） | Elastic License 2.0（非 OSI 开源） |
| Langtrace、Grafana 栈 | AGPL-3.0 |
| lakeFS Community | Business Source License（2026 年转） |
| Elasticsearch | Elastic/SSPL |
| TabPFN 默认权重 | 非商业 |
| SureChEMBL 数据 | CC BY-SA 3.0（share-alike，需法务评审） |
| Pinecone、LangSmith 后端 | 专有 |

---

## 6. 建议的实施顺序（工程视角）

1. **检索连接器**：抽象 connector 接口 → EPO OPS（专利）+ OpenAlex/Unpaywall（文献），现有抓取做 fallback。
2. **解析与向量**：Docling 定为默认解析 → Qwen3-Embedding-0.6B 替换 TF-IDF → Qdrant（或 pgvector）替换 FAISS。
3. **检索质量**：RRF 混合 + Qwen3-Reranker top 重排 → DeepEval golden 集进 CI。
4. **配方优化**：BayBE 接入替换 numpy UCB → qNEHVI 多目标 → MLflow 跟踪实验。
5. **知识库**：自研 claim 级 Wiki 生成（STORM 方法论）+ 引用蕴含校验。
6. **可观测**：Langfuse/Opik 上线，按需 DVC 管数据版本。

每一步都应先有根因/基线分析、改后跑全量测试保持全绿，再进入下一步。
