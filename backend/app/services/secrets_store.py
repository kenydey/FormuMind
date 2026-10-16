"""Persist API secrets to the project ``.env`` file and sync runtime overlay."""
from __future__ import annotations

from .errors import log_handled_exception
import logging
import os
import re
from pathlib import Path

from ..config import Settings, get_settings, resolve_env_path
from .runtime_secrets import (
    effective_setting,
    get_runtime_secrets,
    secret_attrs_from_registry,
)
from .http_safe import make_client

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
    ("custom_api_key", "FORMUMIND_CUSTOM_API_KEY", "自定义端点（HF Endpoints 等）", "llm"),
    ("vision_api_key", "FORMUMIND_VISION_API_KEY", "视觉模型（独立于文本时）", "llm"),
    ("serpapi_api_key", "FORMUMIND_SERPAPI_API_KEY", "SerpAPI", "search"),
    ("tavily_api_key", "FORMUMIND_TAVILY_API_KEY", "Tavily", "search"),
    ("epo_consumer_key", "FORMUMIND_EPO_CONSUMER_KEY", "EPO OPS Consumer Key", "patent"),
    ("epo_consumer_secret", "FORMUMIND_EPO_CONSUMER_SECRET", "EPO OPS Consumer Secret", "patent"),
    ("openalex_mailto", "FORMUMIND_OPENALEX_MAILTO", "OpenAlex mailto", "research"),
    ("openalex_api_key", "FORMUMIND_OPENALEX_API_KEY", "OpenAlex 内容库（全文 PDF/TEI XML）", "research"),
    ("semantic_scholar_api_key", "FORMUMIND_SEMANTIC_SCHOLAR_API_KEY", "Semantic Scholar API Key", "research"),
    ("unpaywall_mailto", "FORMUMIND_UNPAYWALL_MAILTO", "Unpaywall 邮箱（开放获取全文查询，缺省沿用 OpenAlex mailto）", "research"),
    ("mineru_api_key", "FORMUMIND_MINERU_API_KEY", "MinerU 文档解析", "parse"),
    ("mineru_base_url", "FORMUMIND_MINERU_BASE_URL", "MinerU API 地址（默认官方云端，私有部署时修改）", "parse"),
    ("datalab_api_url", "FORMUMIND_DATALAB_API_URL", "Datalab API URL", "infra"),
    ("datalab_api_token", "FORMUMIND_DATALAB_API_TOKEN", "Datalab API Token（DATALAB-API-KEY）", "infra"),
    ("langfuse_public_key", "FORMUMIND_LANGFUSE_PUBLIC_KEY", "Langfuse Public Key", "observability"),
    ("langfuse_secret_key", "FORMUMIND_LANGFUSE_SECRET_KEY", "Langfuse Secret Key", "observability"),
    ("langfuse_host", "FORMUMIND_LANGFUSE_HOST", "Langfuse 地址（默认 cloud.langfuse.com，自托管时修改）", "observability"),
]

# Entries that are addresses or contact e-mails, not credentials: shown in full, edited as plain text. (A key is masked.)
NON_SECRET_ATTRS = frozenset(
    {"openalex_mailto", "unpaywall_mailto", "datalab_api_url", "mineru_base_url", "langfuse_public_key", "langfuse_host"}
)
# Entries whose value is an address that credentials are sent to: validated like the Datalab URL.
_SERVICE_URL_ATTRS = {
    "datalab_api_url": "Datalab API URL",
    "mineru_base_url": "MinerU API 地址",
    "langfuse_host": "Langfuse 地址",
}

_ATTR_BY_ENV = {env: attr for attr, env, _, _ in SECRET_REGISTRY}
_ATTR_BY_ID = {attr: (env, label, group) for attr, env, label, group in SECRET_REGISTRY}
_SECRET_ATTRS = secret_attrs_from_registry(SECRET_REGISTRY)


# Mutable LLM runtime settings persisted alongside the secrets so provider /
# model / base-URL choices survive restarts (settings_attr -> env key).
_LLM_RUNTIME_ENV: dict[str, str] = {
    "llm_provider": "FORMUMIND_LLM_PROVIDER",
    "llm_model": "FORMUMIND_LLM_MODEL",
    "llm_base_url": "FORMUMIND_LLM_BASE_URL",
    "custom_base_url": "FORMUMIND_CUSTOM_BASE_URL",
    # The vision role, same treatment. vision_model used to live here only as an
    # env var, so a value chosen anywhere but .env vanished on restart.
    "vision_provider": "FORMUMIND_VISION_PROVIDER",
    "vision_model": "FORMUMIND_VISION_MODEL",
    "vision_base_url": "FORMUMIND_VISION_BASE_URL",
}

# Flags that decide deployment topology rather than product behaviour, and so
# belong to the operator even though they are toggleable in the Settings UI.
#
# FORMUMIND_CELERY_EAGER decides whether tasks run in the API process or go to
# the broker — that is, whether the worker container does anything at all.
# Promoted like an ordinary feature flag, a value saved once in the UI outranks
# `environment: FORMUMIND_CELERY_EAGER: "false"` in docker-compose.yml forever,
# and a multi-container deployment silently degrades to a single process with
# an idle worker beside it. The symptom is remote: long research runs now
# execute inside the HTTP request, so they can outlast an nginx read timeout.
_TOPOLOGY_FLAGS = frozenset({"FORMUMIND_CELERY_EAGER"})


def _mask(value: str | None) -> str:
    """Enough of a secret to recognise it, never enough to help guess it: the last four characters, and nothing at all
    for anything of 16 characters or fewer (a 12-character key would otherwise show two thirds of itself)."""
    if not value:
        return ""
    if len(value) <= 16:
        return "••••"
    return f"••••{value[-4:]}"


def check_env_value(value: str, *, what: str = "value") -> str:
    """Reject a value that cannot live on one ``.env`` line.

    A newline in a saved API key or model name would otherwise end the line and start another ``KEY=value`` entry, which
    the next start reads as configuration (``FORMUMIND_API_AUTH_ENABLED=false``, ``FORMUMIND_DEPS_INSTALL_ENABLED=true``…).
    """
    if any(c in value for c in "\r\n\0"):
        raise ValueError(f"{what} must not contain line breaks or NUL characters")
    return value


# Link-local (cloud metadata), unspecified and multicast addresses are never a Datalab server; internal and loopback
# addresses are the normal deployment (a compose service name, host.docker.internal, localhost), so they stay allowed.
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata", "instance-data"})


def validate_service_url(url: str, *, what: str = "URL") -> str:
    """A service address that an API key is sent to: a plain http(s) server address, nothing that reaches cloud metadata."""
    import ipaddress
    from urllib.parse import urlparse

    check_env_value(url, what=what)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{what} must start with http:// or https://")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"{what} has no host name")
    if parsed.username or parsed.password:
        raise ValueError(f"{what} must not carry credentials")
    if host in _METADATA_HOSTS:
        raise ValueError(f"{what} points at a cloud metadata service")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise ValueError(f"{what} must not be a link-local, unspecified or multicast address")
    return url


def validate_datalab_url(url: str) -> str:
    """The Datalab URL receives the ``DATALAB-API-KEY`` header on every call."""
    return validate_service_url(url, what="Datalab API URL")


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    check_env_value(value)
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
    """Upsert env keys in ``.env`` (create file if missing).

    The file holds every API key, so it is written like a secret: under a lock (two concurrent saves used to read the same
    old file and drop each other's keys), through a temp file and a rename (a crash can no longer leave it truncated), and
    readable by the owner only.
    """
    import contextlib

    from ._filelock import lock_exclusive, unlock
    from ._fsutil import atomic_write_text

    for key, val in updates.items():
        check_env_value(val or "", what=key)  # before anything is written
    path = path or resolve_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+", encoding="utf-8") as lock_file:
        lock_exclusive(lock_file)
        try:
            text = _merged_env_text(path, updates)
            try:
                atomic_write_text(path, text)
            except OSError:
                # A single file bind-mounted into a container cannot be replaced by rename: write it in place instead.
                path.write_text(text, encoding="utf-8")
            with contextlib.suppress(OSError):  # no-op / unsupported on some filesystems and on Windows
                os.chmod(path, 0o600)
        finally:
            unlock(lock_file)


def _merged_env_text(path: Path, updates: dict[str, str]) -> str:
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
    return "\n".join(new_lines).rstrip() + "\n"


def bootstrap_runtime_secrets(settings: Settings | None = None) -> None:
    """Load secret values from Settings into the thread-safe runtime overlay."""
    s = settings or get_settings()
    get_runtime_secrets().load_from_settings(s, _SECRET_ATTRS)


def apply_persisted_ui_settings() -> None:
    """Lift UI-managed keys from the runtime env file into ``os.environ``.

    Real environment variables outrank dotenv values in pydantic-settings, so
    in Docker a stale key injected at container creation (compose ``env_file``)
    would silently beat whatever the user last saved through the Settings UI.
    UI-saved values are the most recent explicit intent, so at startup we
    promote exactly the managed keys — API secrets, LLM provider/model/base
    URL, and boolean feature flags — from the runtime env file into the
    process environment. Operator-level keys (DB / Redis / auth) are never
    touched, and neither are the topology flags below when the deployment has
    already set them.
    """
    from .env_flags import FLAG_REGISTRY

    managed = {env for _, env, _, _ in SECRET_REGISTRY}
    managed |= set(_LLM_RUNTIME_ENV.values())
    managed |= {flag.env_key for flag in FLAG_REGISTRY}
    try:
        saved = read_env_file()
    except Exception as exc:
        log_handled_exception(logger, exc, "apply_persisted_ui_settings: read failed")
        return

    applied: list[str] = []
    for key in sorted(k for k in saved if k in managed):
        if key in _TOPOLOGY_FLAGS and key in os.environ:
            # The operator said otherwise, out loud, in the deployment config.
            logger.warning(
                "%s=%s saved in the UI is ignored: the deployment sets it to %s",
                key, saved[key], os.environ[key],
            )
            continue
        os.environ[key] = saved[key]
        applied.append(key)
    if applied:
        get_settings.cache_clear()
        logger.info(
            "Restored %d persisted UI settings from %s", len(applied), resolve_env_path()
        )


def persist_llm_runtime() -> None:
    """Persist the effective LLM provider/model/base URL to the env file.

    The runtime overlay applies changes immediately; this write makes them
    survive restarts. Empty values remove the key (``write_env_updates``
    drops falsy entries), so clearing the base URL falls back to defaults.
    """
    s = get_settings()
    writes: dict[str, str] = {}
    for attr, env_key in _LLM_RUNTIME_ENV.items():
        value = effective_setting(s, attr)
        writes[env_key] = str(value) if value else ""
    try:
        write_env_updates(writes)
    except OSError as exc:
        # Read-only FS etc. — the runtime overlay still applied for this run.
        logger.warning("LLM settings: .env persistence failed (%s)", exc)


def reload_settings() -> Settings:
    apply_persisted_ui_settings()
    get_settings.cache_clear()
    s = get_settings()
    bootstrap_runtime_secrets(s)
    return s


def list_secret_status(settings: Settings | None = None) -> list[dict]:
    s = settings or get_settings()
    items: list[dict] = []
    for attr, env_key, label, group in SECRET_REGISTRY:
        val = effective_setting(s, attr)
        items.append(
            {
                "id": attr,
                "env_key": env_key,
                "label": label,
                "group": group,
                "set": bool(val),
                # An address or an e-mail is not a secret: show it, so the user can see what is configured.
                "masked": (str(val) if val else "") if attr in NON_SECRET_ATTRS else _mask(str(val) if val else None),
                "secret": attr not in NON_SECRET_ATTRS,
            }
        )
    return items


def update_secrets(updates: dict[str, str | None]) -> list[str]:
    """Persist secrets by settings attribute id. Returns updated attr ids."""
    rs = get_runtime_secrets()
    env_writes: dict[str, str] = {}
    updated: list[str] = []

    # Validate everything first: a rejected value must not leave half of the batch applied in the runtime overlay.
    cleaned: dict[str, str] = {}
    for attr, raw in updates.items():
        if attr not in _ATTR_BY_ID:
            continue
        value = (raw or "").strip()
        check_env_value(value, what=attr)
        if attr in _SERVICE_URL_ATTRS and value:
            validate_service_url(value, what=_SERVICE_URL_ATTRS[attr])
        cleaned[attr] = value

    for attr, value in cleaned.items():
        env_key, _, _ = _ATTR_BY_ID[attr]
        rs.set(attr, value or None)
        env_writes[env_key] = value
        updated.append(attr)

    if env_writes:
        write_env_updates(env_writes)
        logger.info("Updated secrets: %s", ", ".join(updated))
    if any(attr.startswith("langfuse_") for attr in updated):
        from .llm_trace import reset_client

        reset_client()  # the cached Langfuse client was built from the old keys / host

    return updated


def probe_secret(secret_id: str) -> dict:
    """Lightweight connectivity probe per secret type."""
    s = get_settings()
    if secret_id == "mineru_api_key":
        # MinerU publishes no token-validation or quota endpoint, so the probe
        # asks for a task that cannot exist and reads the answer.
        from .mineru_cloud import probe_token

        ok, message = probe_token()
        return {"ok": ok, "message": message}

    if secret_id == "serpapi_api_key":
        key = effective_setting(s, "serpapi_api_key")
        if not key:
            return {"ok": False, "message": "SerpAPI key 未配置"}
        try:
            with make_client(timeout=15.0) as client:
                r = client.get(
                    "https://serpapi.com/account",
                    params={"api_key": key},
                )
            return {"ok": r.status_code == 200, "message": "SerpAPI 连接成功" if r.status_code == 200 else r.text[:120]}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    if secret_id == "tavily_api_key":
        key = effective_setting(s, "tavily_api_key")
        if not key:
            return {"ok": False, "message": "Tavily key 未配置"}
        try:
            with make_client(timeout=15.0) as client:
                r = client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": key, "query": "zinc phosphate coating", "max_results": 1},
                )
            return {"ok": r.status_code == 200, "message": "Tavily 连接成功" if r.is_success else r.text[:120]}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    if secret_id in ("epo_consumer_key", "epo_consumer_secret"):
        epo_key = effective_setting(s, "epo_consumer_key")
        epo_secret = effective_setting(s, "epo_consumer_secret")
        if not (epo_key and epo_secret):
            return {"ok": False, "message": "EPO Key/Secret 需同时配置"}
        # Ask OPS for a token: the cheapest call that proves the pair is accepted (it used to say "configured" and
        # leave the first real search to find out).
        from . import epo_ops

        try:
            ttl = epo_ops.check_credentials(epo_key, epo_secret)
        except epo_ops.EpoOpsError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception as exc:  # network, TLS, proxy: say what happened rather than fail the page
            return {"ok": False, "message": f"EPO OPS 连接失败：{type(exc).__name__}"}
        return {"ok": True, "message": f"EPO OPS 连接成功（令牌有效期约 {int(ttl // 60)} 分钟）"}

    if secret_id == "openalex_mailto":
        if effective_setting(s, "openalex_mailto"):
            return {"ok": True, "message": "OpenAlex mailto 已配置"}
        return {"ok": False, "message": "OpenAlex mailto 未配置（推荐填写以进入礼貌池）"}

    if secret_id == "unpaywall_mailto":
        if effective_setting(s, "unpaywall_mailto"):
            return {"ok": True, "message": "Unpaywall 邮箱已配置"}
        if effective_setting(s, "openalex_mailto"):
            return {"ok": True, "message": "未单独配置，沿用 OpenAlex mailto"}
        return {"ok": False, "message": "未配置：开放获取全文查询（Unpaywall）需要一个联系邮箱"}

    if secret_id == "semantic_scholar_api_key":
        from .scholar_helpers import SEMANTIC_SCHOLAR_API, semantic_scholar_headers

        if not effective_setting(s, "semantic_scholar_api_key"):
            return {"ok": False, "message": "未配置（可选：不填时与所有匿名用户共享限速，高峰期会 429）"}
        try:
            with make_client(timeout=15.0) as client:
                r = client.get(
                    f"{SEMANTIC_SCHOLAR_API}/paper/search",
                    params={"query": "zinc phosphate coating", "limit": 1, "fields": "title"},
                    headers=semantic_scholar_headers(),
                )
        except Exception as exc:
            return {"ok": False, "message": f"Semantic Scholar 连接失败：{type(exc).__name__}"}
        if r.status_code == 200:
            return {"ok": True, "message": "Semantic Scholar 连接成功，密钥有效"}
        if r.status_code in (401, 403):
            return {"ok": False, "message": "Semantic Scholar 拒绝了该密钥（无效或已停用）"}
        if r.status_code == 429:
            return {"ok": False, "message": "Semantic Scholar 限流（429）：密钥可能无效，或稍后再试"}
        return {"ok": False, "message": f"Semantic Scholar 返回 HTTP {r.status_code}"}

    if secret_id in ("langfuse_public_key", "langfuse_secret_key", "langfuse_host"):
        have = [effective_setting(s, a) for a in ("langfuse_public_key", "langfuse_secret_key")]
        if not all(have):
            return {"ok": False, "message": "Langfuse 需要同时配置 Public Key 与 Secret Key"}
        if not s.langfuse_enabled:
            return {"ok": True, "message": "密钥已保存；在「功能开关」中启用 Langfuse 追踪后生效"}
        return {"ok": True, "message": "密钥已保存，下一次 LLM 调用起上报追踪"}

    if secret_id in ("mineru_base_url", "datalab_api_token"):
        if effective_setting(s, secret_id):
            return {"ok": True, "message": "已配置"}
        return {"ok": False, "message": "未配置（使用默认值）" if secret_id == "mineru_base_url" else "未配置（Datalab 未启用鉴权时可留空）"}

    if secret_id.endswith("_api_key") and secret_id in _ATTR_BY_ID:
        if effective_setting(s, secret_id):
            return {"ok": True, "message": "密钥已保存（使用大模型 Tab 测试连接）"}
        return {"ok": False, "message": "密钥未配置"}

    return {"ok": False, "message": f"未知密钥类型: {secret_id}"}
