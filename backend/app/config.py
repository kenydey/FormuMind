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
    llm_max_tokens: int = 2048
    llm_timeout_seconds: float = 60.0

    # Celery / Redis. Without a reachable broker the worker runs eagerly
    # (synchronously, in-process) which keeps the API usable everywhere.
    redis_url: str = "redis://localhost:6379/0"
    celery_eager: bool = True

    # CORS origins for the Vite dev server.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Optimization loop defaults.
    optimize_iterations: int = 24
    top_n_formulas: int = 5

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

    # 检索设置
    search_limit_per_source: int = 50    # 每源单页大小（增量翻页，实际不设硬上限）
    search_total_limit: int = 300        # 全部来源合并后的总量上限（按相关性排序截断）
    # RAG 检索后端：auto（ColBERT > sentence-transformers > TF-IDF）/
    # colbert / embedding / tfidf。缺库时一律回退 TF-IDF。
    rag_backend: str = "auto"

    # ColBERT 持久知识库
    colbert_index_dir: str = "./data/colbert_index"
    colbert_model: str = "colbert-ir/colbertv2.0"
    colbert_collection: str = "default"
    colbert_top_k: int = 12
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
    arxiv_domain_filter: bool = True
    arxiv_search_enabled: bool = True
    openalex_enabled: bool = True

    # 检索结果内容过滤（KB P0）：规则层默认开启（保守规则：垃圾域名/
    # 空洞摘要/近重复 SimHash）；LLM 批量质量判定默认关闭（每次检索一次调用）。
    content_filter_enabled: bool = True
    content_filter_min_snippet_chars: int = 40
    content_filter_blocked_domains: list[str] = Field(default_factory=list)
    content_filter_llm_judge: bool = False

    # Search-stream LLM rerank (Phase B R-2a): one batched relevance pass on the
    # final ranked list before returning to the client. Degrades to rank order.
    search_rerank_enabled: bool = True
    search_rerank_top_k: int = 20

    # Recommend path: federated refresh → ColBERT index before CRAG recommend.
    auto_kb_refresh_before_recommend: bool = False

    # PDF 解析器层级（KB P1）："auto" = marker → MinerU → MarkItDown → pypdf
    # 逐级回退；指定名称则固定首选（仍向下回退）。marker/MinerU 为重型可选
    # 依赖（版面感知、表格保真的真 Markdown 输出）。
    pdf_parser: str = "auto"
    # 扫描件 OCR（MinerU 管线；需 OCR 依赖，慢但能读图片型 PDF）。
    pdf_ocr: bool = False
    # 公式增强（Docling）：显示公式转 LaTeX $$…$$，化学反应方程式保真。
    # 首次使用会额外下载公式识别模型。
    pdf_formula_enrichment: bool = True

    # 检索结果全文获取（KB P0）：把摘要级命中升级为全文分块并持久化原文。
    # 专利 PDF（USPTO/EPO/Google）+ OA 文献 PDF（OpenAlex/arXiv）+ 网页正文
    # （trafilatura 优先）。默认关闭以保证测试离线；生产建议开启。
    fulltext_enrich: bool = False
    fulltext_max_docs: int = 8
    fulltext_timeout_s: float = 20.0

    # 异步入库队列（KB stream P0）：检索/深度研究/推荐收尾后，后台任务逐篇
    # 获取全文 → 解析 → 切块 → 入持久知识库，前台经 SSE 实时看到每篇状态，
    # 检索结果展示不等待解析。按 origin_url / 内容哈希双重去重。
    kb_ingest_auto: bool = True
    kb_ingest_max_docs: int = 12
    kb_ingest_min_relevance: float = 0.0  # 0 = off; e.g. 0.5 filters low-relevance rows
    workbench_auto_train: bool = True  # Completed workbench rows → ModelRegistry on sync
    auto_loop_on_sync: bool = False  # After sync ingests training rows, dispatch closed-loop task
    # Closed-loop RMSE plateau detection (Phase C L-2): skip optimize+DOE when flat.
    loop_convergence_enabled: bool = True
    loop_convergence_eps: float = 0.01
    loop_convergence_patience: int = 2

    # Knowledge graph P0 — entity index + enumerative RAG (default off for CI).
    kg_enabled: bool = False
    kg_link_on_ingest: bool = True
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

    # 持久知识库 v2（KB P2）：每个 SourceDocument 结构感知切块入
    # document_chunks 表（装了 sentence-transformers 则带归一化向量），
    # 问答检索覆盖整个累计语料而非单次请求携带的 sources。纯本地无网络。
    kb_v2_enabled: bool = True
    kb_max_chunks_per_source: int = 200
    kb_search_scan_limit: int = 5000
    kb_chat_top_k: int = 6
    # 推荐/研究图检索时并入的持久 KB chunk 数（0 = 关闭该融合）。
    kb_recommend_top_k: int = 4

    # 化学/产品实体抽取（KB stream P2）：入库切块时识别 CAS/分子式/SMILES/反应式
    # 与商业牌号（规则层离线；LLM 层搭 source_guide 便车），写入 chunk 元数据与
    # kb_products 产品登记簿。
    chem_extract_enabled: bool = True
    product_extract_enabled: bool = True
    # 图片结构化解析（VLM）：上传图片→表格 Markdown / 分子结构图→SMILES（RDKit 验证）。
    # 复用当前激活的 LLM 供应商；vision_model 为空时用当前模型（需具备视觉能力）。
    vision_extract_enabled: bool = True
    vision_model: str = ""

    # Source Guide LLM extraction (ingest pipeline)
    source_guide_enabled: bool = True
    source_guide_max_chars: int = 12000
    ingest_max_chunks: int = 40
    ingest_chunk_max_chars: int = 1600
    ingest_chunk_overlap: int = 200

    # DOE workbench / campaign persistence (Headless ELN)
    campaign_backend: str = "sqlite"  # sqlite (dev/CI) | datalab (enterprise ELN SSOT) | auto (dev probe)
    datalab_api_url: str = "http://localhost:5001"
    datalab_timeout_seconds: float = 30.0
    datalab_max_connections: int = 10
    datalab_max_keepalive_connections: int = 5
    datalab_required: bool = False  # when True with datalab backends, unreachable → hard fail

    # Experiment training persistence (Headless ELN)
    experiment_backend: str = "sqlite"  # datalab | sqlite (dev/CI)

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
        from .services.runtime_secrets import effective_setting, get_runtime_secrets

        rs = get_runtime_secrets()
        provider = effective_setting(self, "llm_provider")
        mapping = {
            "anthropic": "anthropic_api_key",
            "openai": "openai_api_key",
            "gemini": "gemini_api_key",
            "groq": "groq_api_key",
            "deepseek": "deepseek_api_key",
            "qwen": "qwen_api_key",
            "moonshot": "moonshot_api_key",
            "minimax": "minimax_api_key",
            "xai": "xai_api_key",
        }
        attr = mapping.get(str(provider))
        if not attr:
            return None
        return effective_setting(self, attr)


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
