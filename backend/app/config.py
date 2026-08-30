"""Application configuration.

All settings are environment-driven with safe offline defaults so the platform
boots and tests run without any external credentials or services.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_ENVS = frozenset({"development", "dev", "test"})
# Env keys read by subsystems but not declared on Settings.
_INFRA_ENV_KEYS = frozenset({
    "FORMUMIND_ENV_FILE",
    "FORMUMIND_TASK_DIR",
    "FORMUMIND_TASK_PROGRESS_DIR",
    # P1 owner Phase2 多用户开关
    "FORMUMIND_MULTI_USER",
    "FORMUMIND_API_TOKENS_JSON",
    # Test-only lifespan fast-path flag (see app/main.py::_skip_lifespan_bootstrap).
    "FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP",
    # Optional Neo4j graph-store adapter (sibling to SQLite KG).
    "FORMUMIND_NEO4J_ENABLED",
    "FORMUMIND_NEO4J_URI",
    "FORMUMIND_NEO4J_USER",
    "FORMUMIND_NEO4J_PASSWORD",
})


def _settings_extra_policy() -> str:
    """Development/test: reject unknown env keys; production: ignore extras."""
    env = os.getenv("FORMUMIND_ENVIRONMENT", "development").strip().lower()
    return "forbid" if env in _DEV_ENVS else "ignore"


def _data_env_path() -> Path:
    """``./data/.env`` — the data dir is CWD-relative by convention (matches
    ``db_url`` / ``colbert_index_dir``) and is volume-mounted in Docker, so an
    env file stored there survives container recreation."""
    return Path("./data").resolve() / ".env"


def resolve_env_path(backend_dir: Path | None = None) -> Path:
    """The single canonical ``.env`` location, shared by reader and writer.

    Historically the Settings UI *wrote* secrets to the repo-root ``.env``
    while pydantic-settings *read* the CWD-relative ``.env`` — two different
    files whenever the server didn't start from the repo root (and always in
    Docker, where writes landed at the container root ``/.env``). Saved
    settings therefore vanished on restart. Resolution order:

    1. ``FORMUMIND_ENV_FILE`` explicit override;
    2. first existing among repo-root ``.env``, ``backend/.env``, ``data/.env``;
    3. creation default: repo-root ``.env`` for a source checkout, or the
       persistent ``data/.env`` when the package sits at a filesystem root
       (Docker image ``/app``) or the parent directory is not writable.
    """
    override = os.environ.get("FORMUMIND_ENV_FILE")
    if override:
        return Path(override)
    backend = backend_dir or Path(__file__).resolve().parents[1]
    workspace = backend.parent
    for candidate in (workspace / ".env", backend / ".env", _data_env_path()):
        if candidate.exists():
            return candidate
    if workspace == Path(workspace.anchor) or not os.access(workspace, os.W_OK):
        return _data_env_path()
    return workspace / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORMUMIND_", env_file=".env", extra="ignore")

    app_name: str = "FormuMind"
    environment: str = "development"

    # LLM (Anthropic Claude). When unset, the LLM service falls back to the
    # deterministic rule-based synthesiser built on the domain knowledge base.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 16384
    llm_timeout_seconds: float = 120.0

    # Celery / Redis. Without a reachable broker the worker runs eagerly
    # (synchronously, in-process) which keeps the API usable everywhere.
    redis_url: str = "redis://localhost:6379/0"
    celery_eager: bool = True

    # CORS origins for the Vite dev server.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Optimization loop defaults.
    optimize_iterations: int = 24
    top_n_formulas: int = 5

    # Multi-formulation recommend P1 — Top-N diversity + trade-off analysis.
    recommend_default_n: int = 6
    recommend_max_n: int = 12
    recommend_diversity_enabled: bool = True
    recommend_diversity_lambda: float = 0.7
    recommend_tradeoff_enabled: bool = True
    recommend_uncertainty_flag: bool = True
    # P0 KG 自进化闭环：台账实测 → 写回 KG 关系（extraction_method="measured"）
    kg_measured_feedback_enabled: bool = True
    # Third priority: per-candidate minimal verification DOE for trade-off
    # recommendations. Generates a small LHS DOE anchored to each Pareto-front
    # / scenario-pick candidate so the chemist can verify predictions with one
    # click into the workbench. Off → no verification DOE generated.
    verification_doe_enabled: bool = True
    verification_doe_n: int = 4

    # Experiment feedback / model training.
    # Measured DOE results are persisted in a SQL database (SQLite by default;
    # point db_url at Postgres etc. for multi-process deployments). Trained
    # models are rebuilt from this dataset on startup, so no model binaries are
    # stored. ``experiments_path`` is retained for one-time migration of legacy
    # JSON datasets into the database.
    db_url: str = "sqlite:///./data/formumind.db"
    experiments_path: str = "./data/experiments.json"
    # Minimum measured samples before a trained model is used for a metric.
    min_train_samples: int = 4
    # Retrain automatically when new experiments are submitted.
    auto_retrain: bool = True

    # 多 LLM 供应商
    llm_provider: str = "anthropic"          # 当前激活的供应商
    llm_base_url: str | None = None          # OpenAI 兼容 API 的自定义 base URL
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None          # Meta via Groq
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    moonshot_api_key: str | None = None      # Kimi
    minimax_api_key: str | None = None
    xai_api_key: str | None = None           # Grok
    # 通用 OpenAI 兼容自定义端点（HF Inference Endpoints / vLLM / TGI）。base_url
    # 与 llm_base_url 分开保存，这样在供应商之间来回切换不会互相覆盖。
    custom_api_key: str | None = None
    custom_base_url: str | None = None

    # 检索设置
    search_limit_per_source: int = 50    # 每源单页大小（增量翻页）；不设单源总量上限
    search_total_limit: int = 300        # 全部来源合并后的总量上限（按相关性排序截断）
    # RAG 检索 + 配方推荐配置
    # GPU 加速 ColBERT 检索开关（需 PyLate + CUDA torch）。
    # True  → PyLate ColBERT (GPU ≥ 4GB VRAM)
    # False → BM25 + FAISS 混合检索 (纯 CPU，零 AVX2 要求)
    gpu_enabled: bool = Field(default=False, description="Enable GPU-accelerated ColBERT retrieval")

    # 配方推荐生成模式
    # "hybrid"   → KB 证据 + LLM 合成（叠加，推荐）
    # "llm_only" → 纯 LLM 推荐（快速，离线，当前回退）
    # "kb_only"  → 仅 KB 检索（无 LLM，降级验证）
    formulation_mode: str = Field(default="hybrid", description="Formulation recommendation mode")

    # 检索后端显式覆盖（覆盖 gpu_enabled 自动检测）
    # "auto" | "pylate" | "bm25_faiss" | "embedding" | "tfidf"
    rag_backend: str = "auto"

    # 语义向量化所用的 sentence-transformer。空 = 用默认值
    # （`sentence-transformers/all-MiniLM-L6-v2`）。
    #
    # 默认值是**英文为主**的模型，而本平台检索的是中文专利 —— 这是当前检索质量
    # 的一个真实短板。中文可选项：
    #     BAAI/bge-small-zh-v1.5   体积小，中文优先
    #     BAAI/bge-m3              多语种，但大得多
    #     moka-ai/m3e-base         中文，中等体积
    #
    # ⚠️ 换模型会让**已有的全部向量作废**（不同模型的向量在不同语义空间，不可比较）。
    # 换完之后必须点「重建索引」；在那之前 `/api/kb/stats` 会报
    # `vector_mode == "stale"` 并给出待重建的切块数，而不是让检索悄悄退回关键词。
    #
    # 默认值故意没有改动：这台机器上没装 sentence-transformers，无法实测中文模型
    # 能否加载、维度是多少，所以不把一个未经验证的默认值推给已有部署。
    embedding_model: str = ""

    # ColBERT 持久知识库
    colbert_index_dir: str = "./data/colbert_index"
    colbert_model: str = "colbert-ir/colbertv2.0"
    colbert_collection: str = "default"
    # 单次检索返回的候选数。深度研究把主题拆成子问题后各自检索再合并，所以
    # 总候选量约为 colbert_top_k × deep_subquestions。
    colbert_top_k: int = 16
    colbert_min_score: float = 0.35

    # CRAG Fallback 默认联邦检索源（逗号分隔 env: FORMUMIND_FEDERATED_SOURCES）
    federated_sources: list[str] = Field(
        default_factory=lambda: ["patents", "literature", "internet"]
    )
    federated_sources_notebooklm: bool = False

    # 可选增强引擎（adapter + 离线回退；缺库或关闭时行为不变）
    # 启动时用 PubChemPy 按化学名补全知识库的 SMILES/分子量（需 intel extra + 网络）。
    enrich_compounds: bool = False
    # 化学类问题路由到 ChemCrow 智能体（需 intel extra + 有效 LLM key）。
    use_chemcrow: bool = True
    # ChemCrow 工具网关（services/chemtools.py）：工具级化学能力
    # （名称→SMILES/CAS、官能团、分子专利预筛、管制/爆炸性筛查）。
    # 缺 chemcrow/rdkit 或关闭开关时所有调用返回中性值，管线行为不变。
    chemtools_enabled: bool = True
    chemtools_timeout_s: float = 10.0
    # v2 特征集：在 v1 特征向量后追加 6 个重量加权 RDKit 分子描述符
    # （MolWt/LogP/TPSA/HBD/HBA/芳环数）。需 rdkit；切换后已训模型需重训
    # （重启后 ModelRegistry 会从存储重训，故重启即可）。默认关闭保证兼容。
    chemtools_descriptor_features: bool = False

    # NotebookLM 作为检索 Source（notebooklm-py 直连库；浏览器会话认证）。
    # 需 `notebooklm` extra + 一次性 `notebooklm login` 生成会话文件。
    # 未启用 / 未登录 / 库未装时 search_notebooklm() 静默返回 []。
    notebooklm_enabled: bool = False
    notebooklm_notebook_id: str | None = None
    notebooklm_storage_path: str = "./data/notebooklm_auth.json"

    # 多智能体事件总线（v0.8）。Redis Pub/Sub 仅作预留：默认关闭，
    # agents.bus.publish() 在关闭 / Redis 不可达 / redis 库缺失时静默 no-op。
    # 为下一阶段重物理计算（physics_jobs 频道）的异步投递做准备。
    agent_bus_enabled: bool = False

    # PDF 全文下载（v0.9）。启用后 DeepResearchEngine 在检索到专利后尝试下载 PDF，
    # 将摘要替换为全文段落，提升 kb_agent 的合成质量。默认关闭以保证测试速度。
    # 需要网络访问 USPTO / EPO / Google Patents 服务器。
    pdf_download: bool = False
    pdf_download_max: int = 3     # 每次研究最多下载几篇专利 PDF

    # 深度研究外部知识库（Phase 2+ 使用；Phase 1 仅读取配置）
    openalex_mailto: str | None = None       # OpenAlex 礼貌池标识
    epo_consumer_key: str | None = None      # EPO OPS API consumer key
    epo_consumer_secret: str | None = None     # EPO OPS API consumer secret
    uspto_api_key: str | None = None           # USPTO Open Data API key

    # 检索增强 API（Phase 0+）
    serpapi_api_key: str | None = None
    tavily_api_key: str | None = None

    # 互联网检索的兜底档：Tavily → SerpAPI → DuckDuckGo。
    # DuckDuckGo 是唯一**不需要 API key** 的一档，所以默认保留——没有任何密钥也
    # 能用是本项目的设计属性（测试套件依赖它离线运行）。
    # 但它的结果质量明显弱于前两档；关掉之后，若没有可用密钥就**诚实地返回空**，
    # 而不是塞一批低质量结果冒充检索成功。
    # 每一档都会把自己的名字写进 `Evidence.source`，所以关它之前可以先看看到底
    # 有多少结果是它给的。
    web_search_allow_ddgs: bool = True
    arxiv_domain_filter: bool = True
    arxiv_search_enabled: bool = True
    openalex_enabled: bool = True

    # 检索结果内容过滤（KB P0）：规则层默认开启（保守规则：垃圾域名/
    # 空洞摘要/近重复 SimHash）；LLM 批量质量判定默认关闭（每次检索一次调用）。
    content_filter_enabled: bool = True
    content_filter_min_snippet_chars: int = 40
    content_filter_blocked_domains: list[str] = Field(default_factory=list)
    content_filter_llm_judge: bool = False
    # LLM 质量判定只检查排序靠前的 N 条（其余直接保留）。全量判定把 161 条
    # 拼进一个 prompt（约 24K 字符）会让 DeepSeek 单次调用耗时数百秒，触发
    # 前端 300s stall；限制到 N 条（约 12K 字符）降到秒级，而尾部低相关结果
    # 即使被丢弃对最终 top-k 影响也小。
    content_filter_llm_judge_max_items: int = 60

    # Search-stream LLM rerank (Phase B R-2a): reorder the head of the ranked list;
    # remaining slots up to search_total_limit are kept in rule-rank order.
    search_rerank_enabled: bool = True
    search_rerank_top_k: int = 100       # 精排后至少保留条数（有足够结果时）
    search_rerank_llm_batch: int = 50    # 送入 LLM 评分的候选数（控制成本）

    # 深度研究：写报告的 LLM 能看到多少证据。原本硬编码 12 条 × 每条 300 字符
    # ≈ 3.6 KB，是「深度研究不够深」最大的单点原因——全文抓回来、切好块、入了
    # 库，到提示词这里又被砍成三分之一。0 = 不限制每条长度。
    deep_report_evidence_count: int = 24
    deep_report_snippet_chars: int = 1200
    # 主题拆解成几个子问题分别检索（1 = 关闭，退回单轮检索）。这是唯一能产生
    # 新检索角度的机制：QueryExpander 只产同义词与 IPC 号，且只在条件性的
    # fallback 里才跑，正常成功路径从不扩展查询。
    deep_subquestions: int = 4
    # 用假设性答案改进检索（HyDE）。生成内容只用于检索，绝不进入报告正文。
    deep_hyde_enabled: bool = True

    # PDF 解析器层级（KB P1）："auto" = hybrid → Docling → marker → MinerU →
    # MarkItDown → pypdf 逐级回退；指定名称则固定首选（仍向下回退）。
    pdf_parser: str = "auto"
    # 版面分析（pymupdf4llm 的 hybrid 层）。实测代价与收益：
    #   开：~350MB 峰值、~176ms/页；关：~150MB、~12ms/页
    # 关掉快 25 倍且省一半内存，单栏页面输出完全一致——但**双栏页面会左右
    # 交错**（"Left column line one about Right column line one about"），两句
    # 无关的话被合成一个 chunk、一个向量。论文与专利多为双栏，所以默认开启，
    # 正确性优先；内存实在紧张的机器可关掉，代价如上。
    pdf_layout_analysis: bool = True
    # pymupdf4llm 版面解析层自带的逐页 OCR。**它的库默认值是 True**，而这一层
    # 跑在解析级联的最前面、对每一份 PDF 都跑——于是只要机器上装了 Tesseract，
    # 每篇文档的每一页都会先被 OCR 一遍，不受任何设置约束。VPS 上实测：一次
    # 51.7s 的 arXiv 解析里约 50s 花在这里，而 kb_ingest 的 7200s 软时限被打满
    # 时，日志里正在跑的也正是它（`OCR on page.number=…`）。
    # 关掉之后扫描件并没有失去出路：`looks_scanned` 会把它们交给 `_parse_scanned`
    # （MinerU OCR）或本地 rapidocr——那才是为扫描件准备的层。
    # ⚠️ 顺带修好一个探测撒谎：`char_count` 是在 `to_markdown()` 把 OCR 结果写回
    # 页面**之后**读的，所以开着 OCR 时 `looks_scanned` 实际含义是"连 OCR 之后
    # 都没有文字"，比"这是扫描件"严格得多，扫描件因此被逐页升级而不是走扫描件
    # 分支。关掉后该信号才回到它声称的语义。
    pdf_local_ocr: bool = False
    # 扫描件 OCR（MinerU 管线；需 OCR 依赖，慢但能读图片型 PDF）。
    pdf_ocr: bool = False
    # 公式增强（Docling）：显示公式转 LaTeX $$…$$，化学反应方程式保真。
    # 首次使用会额外下载公式识别模型。
    pdf_formula_enrichment: bool = True

    # ── MinerU 云端解析 ──────────────────────────────────────────────────
    # 把本地解析不好的**单页**（密集表格、公式、图表）升级到 MinerU 云端。
    # ⚠️ 被升级的页面会上传至 mineru.net（第三方），故默认关闭，必须显式开启。
    # 本地 OCR（RapidOCR / ONNX Runtime，纯 CPU，模型随 wheel 分发无需下载）。
    # 扫描件没有文字层，此前只能靠 MinerU 云端 OCR；未配置 MinerU 时整篇无法入库。
    rapidocr_enabled: bool = True
    # 实测：峰值内存随像素数走（120dpi 372MB / 150dpi 557MB / 200dpi 659MB），
    # 而更高 DPI 并没有更准——只是把一行切成更多段。150 是留给真实噪声扫描件的
    # 折中值。
    rapidocr_dpi: int = 120
    rapidocr_max_pages: int = 30
    # ORT 默认用满所有核心，会在 OCR 期间把 web worker 饿死。线程数不影响峰值
    # 内存（实测 4/1/2 线程都是 ~656MB），只影响单页耗时。
    rapidocr_threads: int = 4
    # 扫描页本地 OCR 的置信度门槛（0..1）。平均置信度 >= 此值才用本地结果，
    # 否则回退 MinerU 结构化解析。0 = 永远用本地（不校验质量）。
    rapidocr_min_confidence: float = 0.75

    mineru_enabled: bool = False
    mineru_api_key: str | None = None
    mineru_base_url: str = "https://mineru.net/api/v4"
    # SDK 侧等待任务完成的上限。解析在线程池里跑，不会阻塞事件循环。
    # 这是**整份文档**（扫描件走 `_parse_scanned`）的额度。
    mineru_timeout_s: float = 300.0
    # **单页升级**的额度，独立于上面那个。逐页升级是串行的，所以超时不是"不阻塞"
    # 而是逐页累加：按 300s × 20 页算，一份文档最坏能空等 6000 秒。网络不通时
    # 把它调大只会把等待翻倍，所以这里给单页一个短得多的额度；整份扫描件的上传
    # 体积大得多，仍用上面的 300s。
    mineru_page_timeout_s: float = 90.0
    # 批量升级：把同一份文档的多个升级页合并为一次 MinerU batch 提交（服务端并行），
    # 替代逐页串行往返——实测多图表 PDF 单页串行要 2-3 分钟/页，批量可 5-10× 提速。
    # 关闭则退回逐页串行（旧行为，保留作一键回退开关）。
    mineru_batch_enabled: bool = True
    # 批量提交的轮询超时（SDK batch 默认 1800s，单页是 300s）。
    mineru_batch_timeout_s: float = 1800.0
    # 连续多少页升级失败就放弃这份文档的升级。没有它，一条不通的网络会被
    # 逐页原样重试一遍——同一个错误付 20 次超时的钱。0 = 不熔断。
    mineru_max_page_failures: int = 3
    # 单文档最多升级多少页——防止一份 200 页扫描件一次吃掉大半日配额。
    # 超出部分保留本地解析结果并记警告。
    mineru_max_pages_per_doc: int = 10
    # 本地预检体积上限（服务端为 200MB）。超限直接不发，省一次往返与配额。
    mineru_max_upload_mb: int = 200
    # 内容哈希缓存目录：同一份文件重复解析既费钱又费时。
    mineru_cache_dir: str = "./data/mineru_cache"
    # ═══ 入库收尾自动清理（省空间）════
    # 源文档全文（source_documents.full_text）在切块完成后冗余——切块已覆盖全文，
    # 检索走 document_chunks + kb_mentions，不再读 full_text（仅 reindex 重建会读）。
    # 入库后清空 full_text，保留行元数据（content_hash 去重 / 左栏列表 / source_guide）。
    prune_source_fulltext: bool = True
    # MinerU 云端解析缓存：content_hash 去重会拦截重复 PDF（解析前就跳过），缓存几乎
    # 没有复用机会，反而累积到数百 MB。开启后 parse 不读不写缓存（历史缓存需手动清）。
    prune_mineru_cache: bool = True
    # 一页图片面积占比超过此值即视为"有值得看的图"，触发升级。
    hybrid_image_area_threshold: float = 0.20
    # 视觉解析断路器：连续 N 次永久性失败（端点暂停/鉴权拒绝）后，
    # 跳过当前文档剩余所有图片的视觉解析，直接降级为占位符。
    # 0 = 不断路（始终尝试）。建议 3。
    vision_max_consecutive_failures: int = 3

    # 检索结果全文获取（KB P0）：把摘要级命中升级为全文分块并持久化原文。
    # 专利 PDF（USPTO/EPO/Google）+ OA 文献 PDF（OpenAlex/arXiv）+ 网页正文
    # （trafilatura 优先）。默认关闭以保证测试离线；生产建议开启。
    fulltext_enrich: bool = False
    fulltext_max_docs: int = 8
    fulltext_timeout_s: float = 20.0

    # 专利全文优先用 Google Patents 落地页的 HTML 正文，而不是 PDF。
    # 三条理由，都实测过：
    #   1. 一次请求（~0.7 s）而不是两次；
    #   2. **完全不需要 OCR** —— 扫描版专利走 RapidOCR 是 ~2 s/页、上限 30 页，
    #      落地页是现成文本，0 秒；
    #   3. `itemprop="abstract|description|claims"` 是结构化分段，比从 PDF 版面
    #      重建的 Markdown 干净；中日文专利还白拿一份英文机器翻译对照。
    # PDF 唯一的优势是图表/化学结构图，而那要靠 MinerU 才能送进视觉模型
    # （`mineru_enabled` 默认关），所以今天从 PDF 拿不到 HTML 拿不到的东西。
    # 设为 false 则优先下载 PDF，落地页无 PDF 时仍回落到 HTML 正文。
    patent_prefer_html: bool = True

    # arXiv 文献优先下载 LaTeX 源码（`/e-print/`）而不是 PDF。
    # 同一篇 100 页论文实测：
    #   PDF    下载 1.17 s + 解析 **51.7 s** ≈ 53 s
    #   源码   下载 0.94 s + 转换  ~0.3 s   ≈ 1.2 s     —— 约 50 倍
    # 51.7 秒的大头是 **RapidOCR 对 7 个图表密集页启动了 OCR**：图多的版面在
    # triage 里看起来就像扫描件。源码路径完全不需要 OCR。
    # 另外公式以 LaTeX 形式保留（`$L(C)=aC^b+c$` 而非 `_aC_<sup>_b_</sup>`），
    # `\section{}` 变成 Markdown 标题，`chunk_markdown` 的 heading_path 直接受益。
    # PDF-only 投稿（无源码）自动回落到原来的 PDF 路径。
    arxiv_prefer_source: bool = True

    # 异步入库队列（KB stream P0）：检索/深度研究/推荐收尾后，后台任务逐篇
    # 获取全文 → 解析 → 切块 → 入持久知识库，前台经 SSE 实时看到每篇状态，
    # 检索结果展示不等待解析。按 origin_url / 内容哈希双重去重。
    kb_ingest_auto: bool = True
    # 每批后台入库最多下载多少篇全文。**0 = 不限制**，即检索到的每一条可获取
    # 全文的资料都入库——这是默认值，因为「搜到了但没入库」对使用者来说就是
    # 数据丢失。设成正数只在需要控制外部请求量/磁盘时才有意义。
    kb_ingest_max_docs: int = 0
    # 并发获取全文的线程数。**瓶颈是解析内存而不是网络**：每篇 PDF 都会走完整
    # 解析级联，pymupdf4llm 峰值约 350 MB、扫描件走 OCR 约 557 MB，所以在
    # 2.2 GB 的机器上 3 路并发已经接近上限。入库（切块+向量+写库）保持串行，
    # 避免 SQLite 写冲突。
    kb_ingest_workers: int = 3
    # SSE 进度流的最大保持时长。构建一个几百篇文档的知识库需要几十分钟，原本
    # Redis 路径 1 小时、无 Redis 回退 120 秒都可能在任务健康运行时切断。
    # 客户端在流关闭后会自动改用轮询，所以这个值只决定「连接保持多久」，
    # 不决定「任务能跑多久」。
    task_stream_timeout_s: float = 21600.0  # 6 小时

    # Celery 单任务时限。此前硬编码为 600 / 900 秒（10 / 15 分钟），这与
    # 「取消超时限制」的其余三层直接矛盾：前端已无墙钟上限、SSE 截止 6 小时、
    # `kb_ingest_max_docs=0` 不限篇数——然后 Celery 在第 15 分钟把任务杀掉。
    # 几百篇的知识库构建必然超过 15 分钟，于是前端安静地盯着一个早已被杀死的任务。
    #
    # 这两个值**必须小于 `task_stream_timeout_s`**：流要比任务活得久，才能把
    # 任务的失败状态报出来。反过来（流先断）就是之前那个「构建中断」的假象。
    #
    # 注意：`soft_time_limit` 是**每个任务**的，与 `celery -c` 并发数无关——
    # 并发只决定一个卡死的任务会占住几个 worker 槽位，不决定单个任务能跑多久。
    celery_soft_time_limit_s: int = 14400  # 4 小时：抛 SoftTimeLimitExceeded，任务可自报
    celery_hard_time_limit_s: int = 18000  # 5 小时：强杀，防止卡死的任务永久占槽（须 < task_stream_timeout_s 21600）
    kb_ingest_min_relevance: float = 0.0  # 0 = off; e.g. 0.5 filters low-relevance rows
    workbench_auto_train: bool = True  # Completed workbench rows → ModelRegistry on sync
    auto_loop_on_sync: bool = False  # After sync ingests training rows, dispatch closed-loop task
    # Closed-loop RMSE plateau detection (Phase C L-2): skip optimize+DOE when flat.
    loop_convergence_enabled: bool = True
    loop_convergence_eps: float = 0.01
    loop_convergence_patience: int = 2

    # Knowledge graph P0 — entity index + enumerative RAG (default off for CI).
    kg_enabled: bool = True
    # 入库时的 KG 链接拆成两段：实体提及（快，枚举 RAG 根基）与关系提取
    # （慢，尤其 LLM 关系每 chunk 一次 API 调用）。默认只做实体，关系后置。
    # kg_link_on_ingest 保留为兼容别名；新逻辑用下面两个字段。
    kg_link_on_ingest: bool = True
    kg_entities_on_ingest: bool = True
    kg_relations_on_ingest: bool = False
    kg_enumerative_scan_limit: int = 500
    kg_enumerative_chunk_cap: int = 200
    kg_enumerative_llm_cap: int = 32
    kg_enumerative_max_sources: int = 8
    kg_hybrid_semantic_k: int = 12
    kg_hybrid_enumerative_k: int = 12
    kg_trade_product_link_min_conf: float = 0.85
    kg_llm_product_hint: bool = False
    kg_chat_entity_refs_on_evidence: bool = True
    kg_element_map_path: str = "app/resources/kg_elements.json"
    kg_relation_extract_enabled: bool = False
    kg_llm_relation_extract: bool = False
    kg_relation_min_confidence: float = 0.55
    kg_multimodal_fusion_enabled: bool = False
    # KG → recommendation ranking weights (second priority). Soft constraints:
    # an INHIBITS relation between skeleton materials multiplies the
    # formulation score (sinks it in ranking, never deletes); SYNERGIZES gives
    # a mild bonus when explicitly enabled. KG disabled → both treated as 1.0.
    kg_inhibits_penalty: float = 0.5
    kg_synergizes_bonus: float = 1.0  # 1.0 = disabled by default
    kg_measured_bonus: float = 1.15  # 实测证据加成：材料有 measured 关系时配方得分提升

    # KG v10 — 文献↔实测矛盾检测
    kg_contradiction_threshold: float = 0.3  # 冲突强度阈值：低于此不标记
    kg_contradiction_demote: bool = True  # 冲突替代候选是否在 discover 排序中置后

    # Chat P0 — multi-turn, structured answers, soft clarification.
    chat_multi_turn_enabled: bool = True
    chat_structured_enabled: bool = True
    chat_clarification_enabled: bool = True
    chat_claim_check_enabled: bool = True
    chat_history_max_turns: int = 12
    chat_rewrite_context_turns: int = 6
    # LLM 精排问答检索候选（无 GPU 时替代 ColBERT 的语义排序）。召回阶段用
    # bm25_faiss/ColBERT 粗排 top-N，再由 LLM 打分精排到 top-k；失败回退原排序。
    chat_rerank_enabled: bool = True
    chat_rerank_candidates: int = 50    # 召回候选数（送入 LLM 打分）
    chat_rerank_top_k: int = 20         # 精排后保留条数
    chat_context_max_chars: int = 12000  # 交给 LLM 的全文 chunk 总字符预算

    # 持久知识库 v2（KB P2）：每个 SourceDocument 结构感知切块入
    # document_chunks 表（装了 sentence-transformers 则带归一化向量），
    # 问答检索覆盖整个累计语料而非单次请求携带的 sources。纯本地无网络。
    kb_v2_enabled: bool = True
    # 每篇文档最多持久化多少切块。这是全文成功抓取之后**唯一**还会静默丢内容
    # 的地方：200 × 1600 ≈ 32 万字符，长专利/综述会被截断。
    kb_max_chunks_per_source: int = 600
    kb_search_scan_limit: int = 5000
    kb_chat_top_k: int = 20
    # 检索命中的切块交给 LLM 之前保留多少字符。切块本身是 1600
    # （ingest_chunk_max_chars），此前这里硬编码 600，等于把已经入库的全文
    # 又砍掉三分之二才给模型看。0 = 不截断，完整交出切块。
    kb_snippet_max_chars: int = 0
    # 推荐/研究图检索时并入的持久 KB chunk 数（0 = 关闭该融合）。
    kb_recommend_top_k: int = 4

    # 化学/产品实体抽取（KB stream P2）：入库切块时识别 CAS/分子式/SMILES/反应式
    # 与商业牌号（规则层离线；LLM 层搭 source_guide 便车），写入 chunk 元数据与
    # kb_products 产品登记簿。
    chem_extract_enabled: bool = True
    product_extract_enabled: bool = True

    # 材料空间：把 RAW_MATERIALS 从模块字面量升级为 materials 表，使成分选择
    # 可在运行时扩展（手工录入 / 从 kb_products 提升），并为逆向设计与材料替代
    # 提供候选池。关闭时 RAW_MATERIALS 退回纯种子字面量，行为与升级前一致。
    material_store_enabled: bool = True
    # 图片结构化解析（VLM）：上传图片→表格 Markdown / 分子结构图→SMILES（RDKit 验证）。
    #
    # 视觉是一个独立的「角色」，可以配到与文本完全不同的供应商上——因为「最好的
    # 文本模型」与「能看图的模型」经常不是同一家：DeepSeek 直接拒绝 image_url，
    # 而它可能正是你想用的文本模型。vision_provider 留空 = 完整跟随文本角色
    # （历史行为）。解析逻辑见 services/llm_roles.py。
    vision_extract_enabled: bool = True
    vision_provider: str = ""                 # 空 = 跟随 llm_provider
    vision_model: str = ""                    # 空 = 跟随该角色的供应商默认/文本模型
    vision_base_url: str | None = None
    vision_api_key: str | None = None         # 空则回落到 {vision_provider}_api_key
    # 视觉超时与文本分开：租用端点的冷启动是分钟级，文本的 60s 必然第一次就失败。
    vision_timeout_seconds: float = 300.0
    # 0 = 跟随 llm_max_tokens。整页表格转录很可能需要更多（截断会让 JSON 解析失败，
    # 表现为整图降级），但我还没有真实专利页的实测数据，所以这里只留旋钮不改默认。
    vision_max_tokens: int = 0
    # 自定义端点 scale-to-zero 冷启动：HF 代理在副本启动期间返回 503，带上
    # X-Scale-Up-Timeout 它会改为挂住请求直到就绪。0 = 不发该头。
    vision_scale_up_timeout: int = 600

    # ═══ OCSR 离线结构识别（MolScribe）════
    # 化学结构图离线识别为 SMILES，作为主路径（免 token，省费用），视觉 LLM 兜底。
    # 运行在独立 molscribe Celery worker（独立 venv，torch-cpu），不占主服务内存。
    ocsr_enabled: bool = False
    # Celery 队列名（独立 worker 消费）
    molscribe_queue: str = "molscribe"
    # MolScribe 单张识别超时。CPU 推理预热后 ~3-8s/张；但 worker 冷启动时首个任务要等
    # 模型加载（~35s）。预热钩子把加载挪到 worker 启动时，仍要覆盖「主 worker 先发任务、
    # molscribe worker 尚未就绪」的窗口，故给 180s 余量。超时后回退视觉 LLM，非硬失败。
    molscribe_timeout_s: float = 180.0

    # Source Guide LLM extraction (ingest pipeline)
    source_guide_enabled: bool = True
    source_guide_max_chars: int = 12000
    ingest_max_chunks: int = 40
    ingest_chunk_max_chars: int = 1600
    ingest_chunk_overlap: int = 200

    # DOE workbench / campaign persistence (Headless ELN)
    campaign_backend: str = "auto"  # auto (probe Datalab → fallback sqlite) | sqlite | datalab
    datalab_api_url: str = "http://localhost:5001"
    datalab_timeout_seconds: float = 30.0
    datalab_max_connections: int = 10
    datalab_max_keepalive_connections: int = 5
    datalab_required: bool = False  # when True with datalab backends, unreachable → hard fail

    # Experiment training persistence (Headless ELN)
    experiment_backend: str = "auto"  # auto → 探测 Datalab → 回退 sqlite; 也可显式: datalab | sqlite

    # API security — unset env defers to environment: off in dev/test, on in production.
    api_auth_enabled: bool | None = None
    api_token: str | None = None
    ingest_max_upload_bytes: int = 20 * 1024 * 1024  # 20 MiB per file

    @model_validator(mode="after")
    def _default_api_auth_for_environment(self) -> "Settings":
        if self.api_auth_enabled is not None:
            return self
        env = self.environment.strip().lower()
        object.__setattr__(self, "api_auth_enabled", env in ("production", "prod"))
        return self

    def get_active_api_key(self) -> str | None:
        """根据 llm_provider 返回对应的 API key（读取 runtime overlay）。"""
        from .services.runtime_secrets import effective_setting

        attr = PROVIDER_KEY_ATTR.get(str(effective_setting(self, "llm_provider")))
        if not attr:
            return None
        return effective_setting(self, attr)


#: provider id → the ``Settings`` attribute holding its API key.
#:
#: Shared by :meth:`Settings.get_active_api_key` and the role resolver
#: (``services/llm_roles.py``) so the two cannot drift apart. A provider missing
#: from this map reads as "no key configured" — which is why ``custom`` has to be
#: here: without it, selecting the custom endpoint for the text role silently
#: looked like a missing key rather than a missing mapping.
PROVIDER_KEY_ATTR: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
    "groq": "groq_api_key",
    "deepseek": "deepseek_api_key",
    "qwen": "qwen_api_key",
    "moonshot": "moonshot_api_key",
    "minimax": "minimax_api_key",
    "xai": "xai_api_key",
    "custom": "custom_api_key",
}


def _audit_formumind_env() -> None:
    """In development/test, fail fast on typoed FORMUMIND_* env keys."""
    if _settings_extra_policy() != "forbid":
        return
    known = {f"FORMUMIND_{name.upper()}" for name in Settings.model_fields}
    known |= _INFRA_ENV_KEYS
    unknown = sorted(k for k in os.environ if k.startswith("FORMUMIND_") and k not in known)
    if unknown:
        raise ValueError(
            "Unknown FORMUMIND_* environment variables: " + ", ".join(unknown)
        )


@lru_cache
def get_settings() -> Settings:
    # Read the CWD .env (legacy behaviour) plus the canonical resolved path;
    # the resolved file — where the Settings UI persists — takes precedence.
    settings = Settings(_env_file=(".env", str(resolve_env_path())))
    _audit_formumind_env()
    return settings
