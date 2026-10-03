"""ChEBI lookup via EBI OLS (fail-open). Wave C chemistry connector depth."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote
from .http_safe import make_client

logger = logging.getLogger(__name__)

_OLS_SEARCH = "https://www.ebi.ac.uk/ols4/api/search"


def lookup_chebi(query: str, *, limit: int = 5, timeout_s: float = 6.0) -> list[dict[str, Any]]:
    """Return [{chebi_id, name, description, iri}] or []."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        params = {
            "q": q,
            "ontology": "chebi",
            "rows": min(max(limit, 1), 10),
            "exact": "false",
        }
        url = f"{_OLS_SEARCH}?q={quote(q)}&ontology=chebi&rows={params['rows']}"
        with make_client(timeout=timeout_s, follow_redirects=True) as client:
            resp = client.get(
                url,
                headers={"Accept": "application/json", "User-Agent": "FormuMind/1.0"},
            )
        if resp.status_code >= 400:
            return []
        data = resp.json() or {}
        docs = (data.get("response") or {}).get("docs") or data.get("docs") or []
        out: list[dict[str, Any]] = []
        for doc in docs[:limit]:
            if not isinstance(doc, dict):
                continue
            short = str(doc.get("obo_id") or doc.get("short_form") or "").upper()
            if short and not short.startswith("CHEBI:"):
                # ols sometimes returns CHEBI_123
                short = short.replace("_", ":")
                if short.startswith("CHEBI") and ":" not in short:
                    short = short.replace("CHEBI", "CHEBI:", 1)
            label = doc.get("label") or doc.get("name") or q
            desc = ""
            descs = doc.get("description") or []
            if isinstance(descs, list) and descs:
                desc = str(descs[0])[:240]
            elif isinstance(descs, str):
                desc = descs[:240]
            out.append(
                {
                    "chebi_id": short or str(doc.get("iri") or "")[:64],
                    "name": str(label),
                    "description": desc,
                    "iri": doc.get("iri"),
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("chebi lookup failed: %s", exc)
        return []
