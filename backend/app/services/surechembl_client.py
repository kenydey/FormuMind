"""SureChEMBL official REST client.

P0: name/SMILES lookup
P1: async structure similarity + documents_for_structures

Base: https://www.surechembl.org/api
Failures degrade to empty results — never raise into API hot paths.
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote, urlencode

from loguru import logger

_DEFAULT_BASE = "https://www.surechembl.org/api"
_TIMEOUT_S = 2.0
_STRUCTURE_BUDGET_S = 5.0
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


def _headers() -> dict[str, str]:
    return {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": _UA}


def _http_get_json(path: str, *, timeout: float = _TIMEOUT_S) -> dict[str, Any] | None:
    try:
        import httpx
    except Exception as exc:
        logger.debug("surechembl: httpx unavailable ({})", exc)
        return None
    url = f"{surechembl_base_url()}{path}"
    try:
        with httpx.Client(timeout=timeout, headers=_headers()) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.debug("surechembl GET {} -> {}", path, resp.status_code)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            if str(data.get("status") or "").upper() not in {"OK", "SUCCESS", ""}:
                if not data.get("data"):
                    return None
            return data
    except Exception as exc:
        logger.debug("surechembl GET {} failed ({})", path, exc)
        return None


def _http_post_json(
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = _TIMEOUT_S,
) -> dict[str, Any] | None:
    try:
        import httpx
    except Exception as exc:
        logger.debug("surechembl: httpx unavailable ({})", exc)
        return None
    url = f"{surechembl_base_url()}{path}"
    try:
        with httpx.Client(timeout=timeout, headers=_headers()) as client:
            resp = client.post(url, json=body or {})
            if resp.status_code != 200:
                logger.debug("surechembl POST {} -> {}", path, resp.status_code)
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("surechembl POST {} failed ({})", path, exc)
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
    raw = _http_get_json(f"/chemical/smiles/{encoded}/", timeout=timeout)
    hit: dict[str, Any] | None = None
    if raw:
        data = raw.get("data")
        if isinstance(data, dict):
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


def _poll_structure_results(
    search_hash: str,
    *,
    limit: int,
    budget_s: float,
) -> tuple[list[dict[str, Any]], str | None]:
    """Poll /search/{hash}/status then /results until finished or budget exhausted."""
    deadline = time.time() + max(0.5, budget_s)
    while time.time() < deadline:
        st = _http_get_json(
            f"/search/{search_hash}/status",
            timeout=min(2.0, max(0.2, deadline - time.time())),
        )
        msg = ""
        if st and isinstance(st.get("data"), dict):
            msg = str(st["data"].get("message") or "")
        if "finish" in msg.lower() or "complete" in msg.lower() or "done" in msg.lower():
            break
        try_early = _http_get_json(
            f"/search/{search_hash}/results?page=1&max_results={max(1, limit)}",
            timeout=min(2.0, max(0.2, deadline - time.time())),
        )
        if try_early and isinstance((try_early.get("data") or {}).get("results"), dict):
            structs = (try_early["data"]["results"].get("structures") or [])
            if structs:
                break
        time.sleep(0.35)

    remain = max(0.3, deadline - time.time())
    res = _http_get_json(
        f"/search/{search_hash}/results?page=1&max_results={max(1, limit)}",
        timeout=remain,
    )
    if not res:
        return [], "structure_results_unavailable"
    data = res.get("data") or {}
    results = data.get("results") if isinstance(data, dict) else None
    structs = []
    if isinstance(results, dict):
        structs = results.get("structures") or []
    rows = [r for r in structs if isinstance(r, dict)]
    return rows, None


def structure_search(
    smiles: str,
    *,
    search_type: str = "similarity",
    threshold: int = 85,
    limit: int = 8,
    budget_s: float = _STRUCTURE_BUDGET_S,
) -> dict[str, Any]:
    """Async structure search → wait for hits.

    Returns ``{hits, search_hash, skipped_reason}``.
    """
    q = (smiles or "").strip()
    if not q:
        return {"hits": [], "search_hash": None, "skipped_reason": "empty_smiles"}
    if not surechembl_enabled():
        return {"hits": [], "search_hash": None, "skipped_reason": "surechembl_disabled"}

    lim = max(1, min(25, int(limit)))
    thr = max(50, min(100, int(threshold)))
    cache_key = f"surechembl:v1:sim:{q}:{search_type}:{thr}:{lim}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    body = {
        "StructureSearchRequest": {
            "struct": q,
            "structSearchType": search_type,
            "maxResults": lim,
            "options": str(thr),
        }
    }
    started = time.time()
    start = _http_post_json("/search/structure", body, timeout=min(3.0, budget_s))
    if not start or not isinstance(start.get("data"), dict):
        # Do not cache transport failures — retry next call.
        return {"hits": [], "search_hash": None, "skipped_reason": "structure_search_start_failed"}

    search_hash = str(start["data"].get("hash") or "").strip() or None
    if not search_hash:
        return {"hits": [], "search_hash": None, "skipped_reason": "structure_search_missing_hash"}

    remain = max(0.5, budget_s - (time.time() - started))
    hits, reason = _poll_structure_results(search_hash, limit=lim, budget_s=remain)
    out = {
        "hits": hits[:lim],
        "search_hash": search_hash,
        "skipped_reason": reason if not hits else None,
    }
    # Cache successful polls and definitive empty result sets; skip timeout empties.
    if hits or reason != "structure_results_unavailable":
        _cache_put(cache_key, out)
    return dict(out)


def documents_for_chemicals(
    chemical_ids: list[str],
    *,
    page: int = 1,
    size: int = 3,
    timeout: float = 3.0,
) -> list[dict[str, Any]]:
    """POST /search/documents_for_structures → patent document hits."""
    ids = [str(x).strip() for x in chemical_ids if str(x).strip()]
    if not ids or not surechembl_enabled():
        return []
    cache_key = f"surechembl:v1:docs:{','.join(ids[:8])}:{page}:{size}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return list(cached)

    qs = urlencode(
        [("chemicalIds", i) for i in ids[:8]]
        + [("page", str(max(1, page))), ("itemsPerPage", str(max(1, min(20, size))))],
        doseq=True,
    )
    raw = _http_post_json(f"/search/documents_for_structures?{qs}", {}, timeout=timeout)
    docs: list[dict[str, Any]] = []
    if raw and isinstance(raw.get("data"), dict):
        results = raw["data"].get("results") or {}
        if isinstance(results, dict):
            docs = [d for d in (results.get("documents") or []) if isinstance(d, dict)]
    return list(_cache_put(cache_key, docs[:size]))
