"""SureChEMBL official REST client (P0: name/SMILES lookup only).

Base: https://www.surechembl.org/api
Failures degrade to empty results — never raise into API hot paths.
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

from loguru import logger

_DEFAULT_BASE = "https://www.surechembl.org/api"
_TIMEOUT_S = 2.0
_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SEC = 12 * 3600
_UA = "FormuMind/surechembl"


def surechembl_enabled() -> bool:
    try:
        from ..config import get_settings

        return bool(get_settings().surechembl)
    except Exception:
        raw = (os.environ.get("FORMUMIND_SURECHEMBL") or "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def surechembl_base_url() -> str:
    try:
        from ..config import get_settings

        base = (get_settings().surechembl_base_url or "").strip()
        if base:
            return base.rstrip("/")
    except Exception:
        pass
    env = (os.environ.get("FORMUMIND_SURECHEMBL_BASE_URL") or "").strip()
    return (env or _DEFAULT_BASE).rstrip("/")


def _cache_get(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if time.time() - ts > _TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: Any) -> Any:
    _CACHE[key] = (time.time(), payload)
    return payload


def clear_surechembl_cache() -> None:
    _CACHE.clear()


def _http_get_json(path: str, *, timeout: float = _TIMEOUT_S) -> dict[str, Any] | None:
    try:
        import httpx
    except Exception as exc:
        logger.debug("surechembl: httpx unavailable ({})", exc)
        return None
    url = f"{surechembl_base_url()}{path}"
    try:
        with httpx.Client(timeout=timeout, headers={"Accept": "application/json", "User-Agent": _UA}) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.debug("surechembl GET {} -> {}", path, resp.status_code)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            if str(data.get("status") or "").upper() not in {"OK", "SUCCESS", ""}:
                # Some envelopes use status OK; treat others as miss unless data present.
                if not data.get("data"):
                    return None
            return data
    except Exception as exc:
        logger.debug("surechembl GET {} failed ({})", path, exc)
        return None


def get_by_name(name: str, *, timeout: float = _TIMEOUT_S) -> list[dict[str, Any]]:
    """GET /chemical/name/{name} → list of chemical metadata dicts."""
    q = (name or "").strip()
    if not q or not surechembl_enabled():
        return []
    cache_key = f"surechembl:v1:name:{q.casefold()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return list(cached)

    encoded = quote(q, safe="")
    raw = _http_get_json(f"/chemical/name/{encoded}", timeout=timeout)
    rows: list[dict[str, Any]] = []
    if raw:
        data = raw.get("data")
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            rows = [data]
    return list(_cache_put(cache_key, rows))


def get_by_smiles(smiles: str, *, timeout: float = _TIMEOUT_S) -> dict[str, Any] | None:
    """GET /chemical/smiles/{smiles}/ → single chemical metadata dict."""
    q = (smiles or "").strip()
    if not q or not surechembl_enabled():
        return None
    cache_key = f"surechembl:v1:smiles:{q}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached) if cached else None

    encoded = quote(q, safe="")
    # Trailing slash required by OpenAPI path `/chemical/smiles/{smiles}/`
    raw = _http_get_json(f"/chemical/smiles/{encoded}/", timeout=timeout)
    hit: dict[str, Any] | None = None
    if raw:
        data = raw.get("data")
        if isinstance(data, dict):
            # Response may be { "<smiles>": {..} } or a flat chem object.
            if "chemical_id" in data or "id" in data or "smiles" in data:
                hit = data
            else:
                inner = data.get(q) or next((v for v in data.values() if isinstance(v, dict)), None)
                if isinstance(inner, dict):
                    hit = inner
    _cache_put(cache_key, hit or {})
    return dict(hit) if hit else None


def health() -> bool:
    """Best-effort liveness against /actuator/health."""
    raw = _http_get_json("/actuator/health", timeout=1.5)
    if not raw:
        return False
    return str(raw.get("status") or "").upper() == "UP"
