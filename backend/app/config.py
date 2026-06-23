"""Application configuration.

All settings are environment-driven with safe offline defaults so the platform
boots and tests run without any external credentials or services.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORMUMIND_", env_file=".env", extra="ignore")

    app_name: str = "FormuMind"
    environment: str = "development"

    # LLM (Anthropic Claude). When unset, the LLM service falls back to the
    # deterministic rule-based synthesiser built on the domain knowledge base.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 2048

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
    # RAG 检索后端：auto（有 sentence-transformers 则用语义嵌入，否则 TF-IDF）/
    # embedding（强制语义）/ tfidf（强制词袋）。缺库时一律回退 TF-IDF。
    rag_backend: str = "auto"

    # 可选增强引擎（adapter + 离线回退；缺库或关闭时行为不变）
    # 启动时用 PubChemPy 按化学名补全知识库的 SMILES/分子量（需 intel extra + 网络）。
    enrich_compounds: bool = False
    # 化学类问题路由到 ChemCrow 智能体（需 intel extra + 有效 LLM key）。
    use_chemcrow: bool = True

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

    # PDF 全文下载（v0.9）。启用后 KnowledgeCohort 在检索到专利后尝试下载 PDF，
    # 将摘要替换为全文段落，提升 kb_agent 的合成质量。默认关闭以保证测试速度。
    # 需要网络访问 USPTO / EPO / Google Patents 服务器。
    pdf_download: bool = False
    pdf_download_max: int = 3     # 每次研究最多下载几篇专利 PDF

    # 深度研究外部知识库（Phase 2+ 使用；Phase 1 仅读取配置）
    openalex_mailto: str | None = None       # OpenAlex 礼貌池标识
    epo_consumer_key: str | None = None      # EPO OPS API consumer key
    epo_consumer_secret: str | None = None     # EPO OPS API consumer secret
    uspto_api_key: str | None = None           # USPTO Open Data API key

    def get_active_api_key(self) -> str | None:
        """根据 llm_provider 返回对应的 API key。"""
        mapping = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
            "deepseek": self.deepseek_api_key,
            "qwen": self.qwen_api_key,
            "moonshot": self.moonshot_api_key,
            "minimax": self.minimax_api_key,
            "xai": self.xai_api_key,
        }
        return mapping.get(self.llm_provider)


@lru_cache
def get_settings() -> Settings:
    return Settings()
