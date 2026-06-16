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
    search_limit_per_source: int = 5

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
