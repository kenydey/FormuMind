"""Persist API secrets to the project ``.env`` file and sync runtime Settings."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

# settings_attr -> (env_var, label, group)
SECRET_REGISTRY: list[tuple[str, str, str, str]] = [
    ("anthropic_api_key", "FORMUMIND_ANTHROPIC_API_KEY", "Anthropic", "llm"),
    ("openai_api_key", "FORMUMIND_OPENAI_API_KEY", "OpenAI", "llm"),
    ("gemini_api_key", "FORMUMIND_GEMINI_API_KEY", "Google Gemini", "llm"),
    ("groq_api_key", "FORMUMIND_GROQ_API_KEY", "Groq", "llm"),
    ("deepseek_api_key", "FORMUMIND_DEEPSEEK_API_KEY", "DeepSeek", "llm"),
    ("qwen_api_key", "FORMUMIND_QWEN_API_KEY", "Qwen", "llm"),
    ("moonshot_api_key", "FORMUMIND_MOONSHOT_API_KEY", "Moonshot (Kimi)", "llm"),
    ("minimax_api_key", "FORMUMIND_MINIMAX_API_KEY", "MiniMax", "llm"),
    ("xai_api_key", "FORMUMIND_XAI_API_KEY", "xAI (Grok)", "llm"),
    ("serpapi_api_key", "FORMUMIND_SERPAPI_API_KEY", "SerpAPI", "search"),
    ("tavily_api_key", "FORMUMIND_TAVILY_API_KEY", "Tavily", "search"),
    ("epo_consumer_key", "FORMUMIND_EPO_CONSUMER_KEY", "EPO OPS Consumer Key", "patent"),
    ("epo_consumer_secret", "FORMUMIND_EPO_CONSUMER_SECRET", "EPO OPS Consumer Secret", "patent"),
    ("uspto_api_key", "FORMUMIND_USPTO_API_KEY", "USPTO Open Data", "patent"),
    ("openalex_mailto", "FORMUMIND_OPENALEX_MAILTO", "OpenAlex mailto", "research"),
    ("datalab_api_url", "FORMUMIND_DATALAB_API_URL", "Datalab API URL", "infra"),
]

_ATTR_BY_ENV = {env: attr for attr, env, _, _ in SECRET_REGISTRY}
_ATTR_BY_ID = {attr: (env, label, group) for attr, env, label, group in SECRET_REGISTRY}


def resolve_env_path() -> Path:
    """Prefer repo-root ``.env``, then ``backend/.env``."""
    override = os.environ.get("FORMUMIND_ENV_FILE")
    if override:
        return Path(override)
    backend = Path(__file__).resolve().parents[2]
    workspace = backend.parent
    if (workspace / ".env").exists():
        return workspace / ".env"
    if (backend / ".env").exists():
        return backend / ".env"
    return workspace / ".env"


def _mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(c in value for c in ' \t#"\''):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or resolve_env_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(stripped)
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[key] = raw
    return out


def write_env_updates(updates: dict[str, str], path: Path | None = None) -> None:
    """Upsert env keys in ``.env`` (create file if missing)."""
    path = path or resolve_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        m = _ENV_LINE_RE.match(stripped) if stripped and not stripped.startswith("#") else None
        if m and m.group(1) in updates:
            key = m.group(1)
            val = updates[key]
            if val:
                new_lines.append(f"{key}={_quote_env_value(val)}")
            seen.add(key)
            continue
        new_lines.append(line)

    for key, val in updates.items():
        if key in seen:
            continue
        if val:
            new_lines.append(f"{key}={_quote_env_value(val)}")

    if not new_lines and not path.exists():
        new_lines.append("# FormuMind runtime secrets (managed via Settings UI)")
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def apply_to_settings(settings: Settings, attr: str, value: str | None) -> None:
    if not hasattr(settings, attr):
        return
    object.__setattr__(settings, attr, value or None)


def sync_secrets_to_os_environ(settings: Settings | None = None) -> None:
    """Mirror select keys for third-party libs (ChemCrow reads SERPAPI_API_KEY)."""
    s = settings or get_settings()
    if s.serpapi_api_key:
        os.environ["SERPAPI_API_KEY"] = s.serpapi_api_key
    if s.epo_consumer_key:
        os.environ["EPO_OPS_CONSUMER_KEY"] = s.epo_consumer_key
    if s.epo_consumer_secret:
        os.environ["EPO_OPS_CONSUMER_SECRET"] = s.epo_consumer_secret


def reload_settings() -> Settings:
    get_settings.cache_clear()
    s = get_settings()
    sync_secrets_to_os_environ(s)
    return s


def list_secret_status(settings: Settings | None = None) -> list[dict]:
    s = settings or get_settings()
    items: list[dict] = []
    for attr, env_key, label, group in SECRET_REGISTRY:
        val = getattr(s, attr, None)
        items.append(
            {
                "id": attr,
                "env_key": env_key,
                "label": label,
                "group": group,
                "set": bool(val),
                "masked": _mask(str(val) if val else None),
            }
        )
    return items


def update_secrets(updates: dict[str, str | None]) -> list[str]:
    """Persist secrets by settings attribute id. Returns updated attr ids."""
    s = get_settings()
    env_writes: dict[str, str] = {}
    updated: list[str] = []

    for attr, raw in updates.items():
        if attr not in _ATTR_BY_ID:
            continue
        env_key, _, _ = _ATTR_BY_ID[attr]
        value = (raw or "").strip()
        apply_to_settings(s, attr, value or None)
        env_writes[env_key] = value
        os.environ[env_key] = value
        updated.append(attr)

    if env_writes:
        write_env_updates(env_writes)
        sync_secrets_to_os_environ(s)
        logger.info("Updated secrets: %s", ", ".join(updated))

    return updated


def probe_secret(secret_id: str) -> dict:
    """Lightweight connectivity probe per secret type."""
    import httpx

    s = get_settings()
    if secret_id == "serpapi_api_key":
        key = s.serpapi_api_key
        if not key:
            return {"ok": False, "message": "SerpAPI key 未配置"}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(
                    "https://serpapi.com/account",
                    params={"api_key": key},
                )
            return {"ok": r.status_code == 200, "message": "SerpAPI 连接成功" if r.status_code == 200 else r.text[:120]}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    if secret_id == "tavily_api_key":
        key = s.tavily_api_key
        if not key:
            return {"ok": False, "message": "Tavily key 未配置"}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": key, "query": "zinc phosphate coating", "max_results": 1},
                )
            return {"ok": r.status_code == 200, "message": "Tavily 连接成功" if r.is_success else r.text[:120]}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    if secret_id in ("epo_consumer_key", "epo_consumer_secret"):
        if s.epo_consumer_key and s.epo_consumer_secret:
            return {"ok": True, "message": "EPO OPS 凭证已配置（完整检索在专利搜索阶段验证）"}
        return {"ok": False, "message": "EPO Key/Secret 需同时配置"}

    if secret_id == "openalex_mailto":
        if s.openalex_mailto:
            return {"ok": True, "message": "OpenAlex mailto 已配置"}
        return {"ok": False, "message": "OpenAlex mailto 未配置（推荐填写以进入礼貌池）"}

    if secret_id.endswith("_api_key") and secret_id in _ATTR_BY_ID:
        if getattr(s, secret_id, None):
            return {"ok": True, "message": "密钥已保存（使用大模型 Tab 测试连接）"}
        return {"ok": False, "message": "密钥未配置"}

    return {"ok": False, "message": f"未知密钥类型: {secret_id}"}
