"""Deterministic scholar helpers — DOI extract / Crossref verify / citation expand.

Inspired by AIPOCH open-science literature-review ``kernel.py`` (stdlib HTTP),
implemented for FormuMind's Python backend without notebook exec.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal
from urllib.parse import quote
from .http_safe import make_client

logger = logging.getLogger(__name__)

DOI_RE = re.compile(
    r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b",
    re.IGNORECASE,
)

NoticeKind = Literal["none", "notice", "retracted_work", "corrected", "unknown"]


def extract_dois(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for m in DOI_RE.finditer(text):
        doi = m.group(0).rstrip(").,;")
        if doi.lower() not in {x.lower() for x in found}:
            found.append(doi)
    return found[:40]


def _classify_notice(update_to: list[Any], updated_by: list[Any]) -> NoticeKind:
    """Map Crossref update relations to a coarse notice_kind."""
    if not update_to and not updated_by:
        return "none"
    blobs: list[str] = []
    for block in list(update_to) + list(updated_by):
        if isinstance(block, dict):
            blobs.append(str(block.get("type") or block.get("label") or "").lower())
            blobs.append(str(block.get("DOI") or block.get("doi") or "").lower())
        else:
            blobs.append(str(block).lower())
    joined = " ".join(blobs)
    if "retract" in joined or "withdrawal" in joined:
        return "retracted_work"
    if "corrig" in joined or "errat" in joined or "correct" in joined:
        return "corrected"
    if update_to or updated_by:
        return "notice"
    return "unknown"


def verify_dois(
    dois: list[str],
    *,
    timeout_s: float = 4.0,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    """Best-effort Crossref verify. Offline / failure → status=unknown.

    Each row includes ``notice_kind`` (Wave C) and legacy ``retracted`` bool.
    """
    out: list[dict[str, Any]] = []
    if not enabled:
        return [
            {
                "doi": d,
                "status": "skipped",
                "title": None,
                "retracted": None,
                "notice_kind": "unknown",
            }
            for d in dois
        ]

    for doi in dois[:20]:
        row: dict[str, Any] = {
            "doi": doi,
            "status": "unknown",
            "title": None,
            "retracted": None,
            "notice_kind": "unknown",
        }
        try:
            url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
            with make_client(timeout=timeout_s, follow_redirects=True) as client:
                resp = client.get(
                    url,
                    headers={"User-Agent": "FormuMind/1.0 (mailto:formumind@example.com)"},
                )
            if resp.status_code == 404:
                row["status"] = "not_found"
                row["notice_kind"] = "none"
                row["retracted"] = False
            elif resp.status_code >= 400:
                row["status"] = "unknown"
            else:
                msg = (resp.json() or {}).get("message") or {}
                row["status"] = "ok"
                titles = msg.get("title") or []
                row["title"] = titles[0] if titles else None
                update_to = msg.get("update-to") or []
                updated_by = msg.get("updated-by") or []
                kind = _classify_notice(update_to, updated_by)
                row["notice_kind"] = kind
                # Legacy bool: true when any correction/retraction notice exists
                row["retracted"] = kind in {"retracted_work", "corrected", "notice"}
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
    retracted_works = [r for r in results if r.get("notice_kind") == "retracted_work"]
    corrected = [r for r in results if r.get("notice_kind") == "corrected"]
    notices = [r for r in results if r.get("notice_kind") == "notice"]
    notes: list[str] = []
    if bad:
        notes.append("未解析 DOI: " + ", ".join(r["doi"] for r in bad))
    if retracted_works:
        notes.append("疑似撤稿文献: " + ", ".join(r["doi"] for r in retracted_works))
    if corrected:
        notes.append("有更正/勘误关联: " + ", ".join(r["doi"] for r in corrected))
    if notices and not retracted_works and not corrected:
        notes.append("有 Crossref 更新通知: " + ", ".join(r["doi"] for r in notices))
    if not notes:
        return answer, results
    footer = "\n\n> ⚠️ **DOI 校验** · " + "；".join(notes)
    if footer.strip() not in answer:
        answer = answer.rstrip() + footer
    return answer, results


def _openalex_headers() -> dict[str, str]:
    mailto = "formumind@example.com"
    try:
        from ..config import get_settings
        from .runtime_secrets import effective_setting

        m = effective_setting(get_settings(), "openalex_mailto")
        if m:
            mailto = str(m)
    except Exception:  # noqa: BLE001
        pass
    return {"User-Agent": f"FormuMind/1.0 (mailto:{mailto})"}


def _openalex_get(url: str, *, timeout_s: float = 6.0) -> dict[str, Any] | None:
    try:
        with make_client(timeout=timeout_s, follow_redirects=True) as client:
            resp = client.get(url, headers=_openalex_headers())
        if resp.status_code >= 400:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("openalex get failed: %s", exc)
        return None


def expand_citations(
    doi: str,
    *,
    n_backward: int = 12,
    n_forward: int = 8,
    timeout_s: float = 6.0,
) -> dict[str, Any]:
    """One OpenAlex citation-graph step (backward references + forward cited-by)."""
    empty = {"doi": doi, "references": [], "cited_by": [], "work_id": None}
    d = (doi or "").strip()
    if not d:
        return empty
    enc = quote(d, safe="")
    work = _openalex_get(
        f"https://api.openalex.org/works/doi:{enc}?select=id",
        timeout_s=timeout_s,
    )
    work_id = ((work or {}).get("id") or "").rsplit("/", 1)[-1]
    if not work_id:
        return empty

    def _rows(results: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for w in results or []:
            if not isinstance(w, dict):
                continue
            raw_doi = (w.get("doi") or "").replace("https://doi.org/", "")
            out.append(
                {
                    "doi": raw_doi,
                    "title": w.get("title"),
                    "year": w.get("publication_year"),
                    "cited_by_count": w.get("cited_by_count"),
                }
            )
        return out

    def _list(filter_expr: str, n: int) -> list[dict[str, Any]]:
        base = (
            f"https://api.openalex.org/works?filter={filter_expr}"
            f"&sort=cited_by_count:desc&per-page={min(max(n, 1), 50)}"
            f"&select=doi,title,publication_year,cited_by_count"
        )
        j = _openalex_get(base, timeout_s=timeout_s)
        return _rows((j or {}).get("results") or [])

    return {
        "doi": d,
        "work_id": work_id,
        "references": _list(f"cited_by:{work_id}", n_backward),
        "cited_by": _list(f"cites:{work_id}", n_forward),
    }


def expand_top_dois(
    dois: list[str],
    *,
    max_seeds: int = 3,
    n_backward: int = 12,
    n_forward: int = 8,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    """Expand up to ``max_seeds`` DOIs; fail-open per seed."""
    if not enabled:
        return []
    out: list[dict[str, Any]] = []
    for doi in (dois or [])[: max(0, int(max_seeds))]:
        try:
            out.append(
                expand_citations(
                    doi, n_backward=n_backward, n_forward=n_forward
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("expand_citations %s failed: %s", doi, exc)
            out.append(
                {"doi": doi, "references": [], "cited_by": [], "work_id": None, "error": str(exc)[:120]}
            )
    return out


def build_evidence_provenance(
    *,
    doi_results: list[dict[str, Any]] | None,
    sourced_claims: list[Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Aggregate doi_status + coarse evidence_availability for ChatResponse."""
    if not enabled:
        return None
    doi_status = list(doi_results or [])
    unsupported = 0
    weak = 0
    supported = 0
    for c in sourced_claims or []:
        st = getattr(c, "status", None) if not isinstance(c, dict) else c.get("status")
        if st == "unsupported":
            unsupported += 1
        elif st == "weak":
            weak += 1
        elif st == "supported":
            supported += 1
    not_found = sum(1 for r in doi_status if r.get("status") == "not_found")
    retracted = sum(1 for r in doi_status if r.get("notice_kind") == "retracted_work")
    notes: list[str] = []
    if not_found:
        notes.append(f"{not_found} 个 DOI 未解析")
    if retracted:
        notes.append(f"{retracted} 个疑似撤稿")
    if unsupported:
        notes.append(f"{unsupported} 条无据断言")
    elif weak:
        notes.append(f"{weak} 条弱支撑")

    if unsupported >= 2 or (unsupported >= 1 and supported == 0):
        availability = "unavailable"
    elif unsupported or weak or not_found or retracted:
        availability = "partial"
    elif supported or doi_status:
        availability = "supported"
    else:
        availability = "unknown"

    return {
        "doi_status": doi_status,
        "evidence_availability": availability,
        "notes": notes,
        "counts": {
            "supported_claims": supported,
            "weak_claims": weak,
            "unsupported_claims": unsupported,
            "doi_not_found": not_found,
            "doi_retracted_work": retracted,
        },
    }
