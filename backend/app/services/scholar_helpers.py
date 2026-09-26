"""Deterministic scholar helpers — DOI extract / optional Crossref verify.

Inspired by AIPOCH open-science literature-review ``kernel.py`` (stdlib HTTP),
implemented for FormuMind's Python backend without notebook exec.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

DOI_RE = re.compile(
    r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b",
    re.IGNORECASE,
)


def extract_dois(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for m in DOI_RE.finditer(text):
        doi = m.group(0).rstrip(").,;")
        if doi.lower() not in {x.lower() for x in found}:
            found.append(doi)
    return found[:40]


def verify_dois(
    dois: list[str],
    *,
    timeout_s: float = 4.0,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    """Best-effort Crossref verify. Offline / failure → status=unknown."""
    out: list[dict[str, Any]] = []
    if not enabled:
        return [{"doi": d, "status": "skipped", "title": None, "retracted": None} for d in dois]
    try:
        import httpx
    except ImportError:
        return [{"doi": d, "status": "unknown", "title": None, "retracted": None} for d in dois]

    for doi in dois[:20]:
        row: dict[str, Any] = {
            "doi": doi,
            "status": "unknown",
            "title": None,
            "retracted": None,
        }
        try:
            url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
            with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "FormuMind/1.0 (mailto:formumind@example.com)"})
            if resp.status_code == 404:
                row["status"] = "not_found"
            elif resp.status_code >= 400:
                row["status"] = "unknown"
            else:
                msg = (resp.json() or {}).get("message") or {}
                row["status"] = "ok"
                titles = msg.get("title") or []
                row["title"] = titles[0] if titles else None
                update_to = msg.get("update-to") or []
                updated_by = msg.get("updated-by") or []
                row["retracted"] = bool(update_to or updated_by)
        except Exception as exc:  # noqa: BLE001
            logger.debug("doi verify %s failed: %s", doi, exc)
            row["status"] = "unknown"
        out.append(row)
    return out


def annotate_answer_dois(answer: str, *, enabled: bool = True) -> tuple[str, list[dict[str, Any]]]:
    """Append a short DOI status footer when verification finds problems."""
    dois = extract_dois(answer)
    if not dois:
        return answer, []
    results = verify_dois(dois, enabled=enabled)
    bad = [r for r in results if r.get("status") == "not_found"]
    retracted = [r for r in results if r.get("retracted")]
    notes: list[str] = []
    if bad:
        notes.append("未解析 DOI: " + ", ".join(r["doi"] for r in bad))
    if retracted:
        notes.append("可能含更正/撤稿关联: " + ", ".join(r["doi"] for r in retracted))
    if not notes:
        return answer, results
    footer = "\n\n> ⚠️ **DOI 校验** · " + "；".join(notes)
    if footer.strip() not in answer:
        answer = answer.rstrip() + footer
    return answer, results
