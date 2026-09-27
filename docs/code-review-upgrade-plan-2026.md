# FormuMind 代码审查驱动的升级方案（2026-09-26）

> 本方案基于对 `~/workspace/FormuMind` 全仓代码的只读审查（后端 4 个分组并行深挖），不是基于 README 的推测。所有结论精确到文件与函数。
> 先说最重要的核实结论：**你提到的 6 项升级中，Qwen3-Embedding 与 Qdrant 在代码中完全不存在；BayBE 代码已接入但本机未安装导致静默降级；Docling 已接入但只是解析级联的第二顺位，并非默认。**

## 0. 声称 vs 实际核实表

| 声称 | 结论 | 证据 |
|---|---|---|
| EPO OPS | ✅ 真实实现 | `services/literature.py:192` `_search_epo_patents` 经 patent_client SDK；无凭证返回空 |
| OpenAlex | ✅ 真实实现 | `services/search_providers.py:298` `search_openalex`（多臂查询、OA 过滤、配额逻辑） |
| Docling | ✅ 真实实现，但**不是默认** | `services/parsing.py:68` 真实调用；但 `_PDF_TIERS`（`parsing.py:352`）第一顺位是 pymupdf4llm 的 hybrid，Docling 排第二；且 venv 未装 docling |
| Qwen3-Embedding | ❌ **代码中零命中** | 实际是 `all-MiniLM-L6-v2` + `bge-small-zh-v1.5`（`rag.py:88-100`）；且 venv 未装 sentence-transformers，语义检索当前未生效 |
| Qdrant | ❌ **被注释明确排除** | `config.py:613`、`hybrid_search.py:11` 原文 "not Qdrant"；持久化 KB 用 SQLite JSON 列 + 进程内 numpy 余弦全扫描 |
| BayBE | ✅ 代码真实接入，**部署未生效** | `services/engines/baybe_engine.py`（472 行，`ContinuousLinearConstraint`、`ParetoObjective` 均为真实约束）；但 venv 未装 baybe，`engine=auto` 静默降级为 numpy-UCB |

---

## 1. P0：本周修复（安全 / 正确性）

### 1.1 正确性地雷

1. **删除 `_RandomProj` 回退**（`services/rag.py:283-300`）
   venv 缺 sentence-transformers 时用**随机向量**建 FAISS 索引，而 chat 主链路（`llm.py:1956`）走 `BM25FAISSStore` 的 `BM25(0.6)+FAISS(0.4)` —— 线上排序 = BM25 + 随机噪声。注释 "still better than no FAISS" 是错的。改为缺包时抛错 → `_faiss_index` 保持 None → 干净降级纯 BM25 + warning 日志。

2. **混料设计映射失真**（`services/engines/adapters/doe_adapter.py:matrix_to_doe_plan`）
   `simplex_lattice` 输出的是行和=1 的**比例**，却被逐因子独立 `decode()` 到各自 `[low, high]` —— "和=100%" 语义被破坏。pydoe 一装上就会触发。为 `simplex_*` 单独写映射（比例 × lever 总和），或从 `PYDOE_DESIGNS` 移除并文档化。

3. **训练集 project 污染**（`services/training.py:ModelRegistry._dataset`）
   空 project_id 的记录会被混入**指定** project 的训练集，而 `_retrain_all` 按 `(project_id, metric)` 建模。统一 project 过滤语义，空 project 记录默认不混入。

4. **装上缺失的 extras，终结"假升级"**
   本机只装了 `[dev,llm]`+`[science]`，baybe / pydoe / optuna(bo) / sentence-transformers(embedding) 全缺。`engine="auto"` 全部静默降级（仅 warning）。执行 `pip install -e '.[baybe,pydoe,optimize,bo,embedding]'`，并在 `/api/meta` 返回各引擎探针结果（`baybe_available()` 等），让前端显示**真实可用**引擎。

### 1.2 安全

5. **SSRF：OA PDF 候选直连无检查**（`services/fulltext_fetcher.py::_fetch_literature_text` ~L434 → `pdf_downloader.py::fetch_pdf_ex`，`follow_redirects=True`）
   `_fetch_web_text` 有 `_is_safe_url` 检查唯独 PDF tier 漏了；DOI/第三方 OA 数据半可控，恶意 302 可打内网。照抄 `_fetch_web_text` 的手动重定向循环（逐跳重检）替代直连。同文件 `_openalex_content_text` 把 api key 放 URL query param 跟随重定向 —— 改走 header。

6. **Auth 静默旁路**（`middleware/api_auth.py::resolve_api_token` + `dispatch`）
   dev token 文件存在但内容为空 → 返回 `None` → `dispatch` 直接放行，`api_auth_enabled=true` 形同虚设且无告警。空文件时应重新生成；auth 启用且 token 为 None 时 401。

7. **`scripts/install.sh:18-19` 删除 chemcrow 安装**
   代码侧 2026-09 已 de-ChemCrow（`main.py` 健康检查显式标 False，pyproject 无声明），但 install.sh 每次重装都会把 chemcrow 0.3.20 装回来 —— 正是它把 openai 从 3.19.2 降级到 0.27.8、导致 2 个 `test_llm_tenacity` 失败的根源。

8. **生产认证默认值被击穿**（`docker-compose.eln.yml`）
   `config.py:713-722` 的 "production 默认开认证" 设计是好的，但三个 compose 文件都写死 `FORMUMIND_API_AUTH_ENABLED: ${...:-false}`，而 eln 文件同时设了 `FORMUMIND_ENVIRONMENT: production` —— 生产 ELN 部署默认无认证。删除显式设置让 config 默认接管，或改为 `:-true`。

9. **Bearer token 烘焙进前端产物**（`docker-compose.yml` frontend `args: VITE_API_TOKEN`）
   Vite 构建时内联进 bundle，任何能下载静态文件的人可读 token。删除 build arg，改由 SettingsModal 运行时录入（`api.ts:1219` `setApiToken` 已有）。

10. **nginx 拦截 20MB 上传**（`frontend/nginx.conf`）
    未设 `client_max_body_size`（默认 1m），而后端允许 20 MiB —— 生产走 nginx 时 >1MB 上传直接 413 且后端无日志。server 块加 `client_max_body_size 25m;`。

11. **摄取孤儿文档**（`services/ingestion.py::_ingest_parsed_text`）
    `source_store.create()` 独立 commit 后 `index_source()` 独立事务（内部吞异常），chunk/embed 失败 → 有 SourceDocument 行但无 chunks。二选一：`api/ingest.py::ingest_document` 改调原子的 `ingest_tx.py::ingest_document_tx`（现无生产调用方），或在异常路径补偿删除孤儿行。

12. **服务端 pip install 端点**（`api/dependencies.py::install_dependencies`）
    持 token 者可触发服务端 pip install。生产默认禁用（环境变量开关），并收紧 `validate_names` 白名单 + 审计日志。

---

## 2. P1：本月工业级

### 2.1 检索与 RAG（把你以为已做的真正做完）

13. **向量检索规模化**：`document_chunks` 全表扫（`all_chunks(limit=5000)`）+ 进程内纯 Python cosine，超 5000 按时间截断，召回有天花板。引入 **Qdrant**（新增 `services/qdrant_store.py`，替换 `hybrid_search.py` 进程内余弦；compose 加服务；`config.py` 加 `FORMUMIND_QDRANT_URL`）或 **pgvector**（若 Postgres 已是主库）。
14. **Embedding 升级 Qwen3-Embedding-0.6B**（Apache-2.0）：替换 `rag.py` 的 MiniLM/bge-small-zh 双语分流为统一多语言模型；GGUF 量化 CPU 推理；先在自有化学语料跑检索基准再切。
15. **cross-encoder reranker**（Qwen3-Reranker-0.6B / BGE-reranker-base）：`rag.py` 新增 `rerank_cross_encoder`，替代/补充 LLM 打分式 `llm_rerank`（后者在 chat 主链路已因 30–76s/问下线）；失败时显式标记未精排（现在 `llm_rerank` 失败静默保留原序，G20）。
16. **引用完整性**：`_build_context`（`llm.py:1590`）引用行带上页码锚点 `[1] (p.3)`，与已实现的 `CitationAnchor.to_citation_text()` 对齐；STORM 长文在 `stitch_and_polish` 后接入 `claim_checker.check_claims`（现在 fail-open 且长文未接线）。
17. **chunk 质量**：`chunking.py` `_is_atomic` 增加 HTML `<table>` 识别（MinerU 云返回的表格会被按空行拆散）；ingest 侧加 SimHash/MinHash 近似去重（现在只按 source_id 幂等）。

### 2.2 配方优化

18. **BayBE 真实启用验证**：部署环境 `python -c "import baybe"` 确认；`active_learning.py` 的 fallback 加 `logger.warning`（现在静默）；删除 `build_genome_searchspace` 死代码或正式启用。
19. **不确定度校准**（`services/training.py:predict_with_std`）：RF 树间 std（小样本系统性偏小）+ ridge RMSE 常数 + 经验侧 15% 都是未校准的，而 EI/UCB 全建在上面。加 conformal 分位数区间（kfold 残差）。
20. **模型版本管理**：`ModelInfo`（`schemas.py:719`）加 `trained_at` / `data_hash` / `feature_version`；模型 joblib 落盘 `data/models/`，支持版本加载与回滚（现在常驻内存，重启全量重训）。
21. **多目标诚实化**：`BotorchOptimizer` 是单目标 LogEI（吃加权分），真正的 Pareto 只在 BayBE 路径保留 —— 要么加 qNEHVI 分支，要么文档明确"标量化 EI"；修正 `dependencies.py:75` 的"Optuna NSGA-II"描述（代码只用了 TPESampler，名不副实）。
22. **虚拟闭环标注**：`run_optimization` 的 observe 用的是 predictor 自己的预测值，history 曲线是代理模型自洽性而非真实改进 —— `OptimizationResult` 加字段标明 measurements 来源。

### 2.3 工程与可观测

23. **部署一致性**：install.sh / Dockerfile / CI 三套依赖路径。install.sh 先 `pip install -r backend/requirements.txt` 再装 extras；`pyproject.toml` 给 numpy 加上界（现在 `>=1.26` 无上界 vs requirements 锁 2.4.6，`.[science]` 已漂到 2.5.3）；为 extras 生成锁文件或 Docker 构建后 `pip freeze` 归档。
24. **CI 补门禁**：`verify_frontend_api.py`（现成的前后端契约审计脚本，实测 0 缺失）接入 `ci.yml`；`ci-deps.yml` 矩阵补 `llm`（install.sh 实际装的 extra，现在不在门禁内）。
25. **生产切 Postgres**：默认 SQLite + 跨进程写锁仅 2 处覆盖 + Redis 不可用静默降级无锁，长事务下 "database is locked" 风险真实。部署文档明确生产用 `FORMUMIND_DB_URL=postgresql://...`。
26. **LLM 可观测**：全仓 langfuse/opik 零命中。引入 Langfuse（MIT core）或 Opik（Apache-2.0），在 `rag.py` / `recommend_pipeline.py` / `literature.py` 加 trace。
27. **评估**：golden 集只有 7 问（关键词命中 + 引用格式），扩到 50+；引入 DeepEval 补 faithfulness/answer-relevancy CI 门禁（与现有 `ci_golden_gate.sh` 并存）。
28. **Celery 韧性**：`celery_app.py` 加 `task_acks_late`、`worker_prefetch_multiplier`；关键任务加 `autoretry_for` + `self.retry()`（`doe_cycle` 的 `max_retries=3` 现在名存实亡）。

---

## 3. P2：排期

- 双语向量空间统一（现在 zh→512d / en→384d 按模型名隔离，跨语言查询退化为关键词；`kb_bilingual` 默认关）
- 混合检索权重统一（`hybrid_search_scored` 用 `kb_hybrid_alpha=0.3`，`BM25FAISSStore` 硬编码 0.6/0.4）
- 统一 `[n]` / `[^n]` 两套引用体系；删除 `build_citation_prompt` 死代码或正式接线
- 补测试：`test_org_api.py`（`GET /api/org/dashboard`）、`test_session_api.py`（5 条路由）—— 284 个测试文件中这两处零覆盖
- `frontend/src/api.ts`（4603 行）按域拆分；前端 E2E（关键链路推荐→DoE→实验同步）
- 约束前移：KG 不相容/酸稳定/合规现在是推荐后标 `infeasible`，采样预算仍花在不可行区 —— 高频拦截项改用 BayBE `DiscreteExcludeConstraint`
- `mailto` 默认值改占位符（现在硬编码个人邮箱，共享导致 OpenAlex 429）
- Alembic 与运行时 `_ensure_*` 软扩列双轨收敛

---

## 4. License 红线（采用前必查）

- **pymupdf4llm / PyMuPDF（AGPL-3.0）是默认解析首选**（`parsing.py` hybrid 第一档，`Dockerfile:44-46` 直接装）—— 闭源商用前必须法务确认或切到 Docling（MIT，需先解决 CPU 权重与安装）
- `pip uninstall -y surya-ocr`（GPL-3.0）靠"装完再卸"规避，非常脆弱，改为构建时 `pip-licenses` 断言
- Jina embeddings（CC BY-NC）、Elasticsearch（SSPL）等此前已列的不再重复

## 5. 明确无问题的项

- 前后端接口契约：211 个前端调用点 vs 后端路由，**零缺失、零方法不匹配**（双工具交叉验证）
- 认证中间件实现扎实（compare_digest、SSE token 限定路径）；SSRF 防护多层（除 P0-5 的 PDF tier 缺口外）
- 无硬编码密钥；`.env` 已 gitignore；密钥日志脱敏
- 后端 284 测试文件 / 前端 81 文件，核心链路覆盖广
