
"""P0 KB ingest audit — SQLite table with JSONL fallback."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def _fingerprint(query: str | None, domain: str | None) -> str:
    # Nested same-quote f-strings are SyntaxError on Python 3.11 (CI).
    raw = f"{(query or '').strip().lower()}::{(domain or '').strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def record_ingest_audit(
    *,
    project_id: str | None,
    domain: str | None,
    query: str | None,
    evidence_id: str | None,
    source: str | None,
    action: str,
    reason: str,
    domain_match: str | None = None,
) -> dict[str, Any]:
    row = {
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "domain": domain,
        "query_fingerprint": _fingerprint(query, domain),
        "evidence_id": evidence_id,
        "source": source,
        "action": action,  # accept | skip
        "reason": reason,
        "domain_match": domain_match,
    }
    if _try_sqlite(row):
        return row
    _append_jsonl(row)
    return row


def _try_sqlite(row: dict[str, Any]) -> bool:
    try:
        from ..db.session import session_scope
        from ..db.models import KbIngestAudit
        with session_scope() as s:
            s.add(
                KbIngestAudit(
                    id=row["id"],
                    created_at=datetime.now(timezone.utc),
                    project_id=row.get("project_id"),
                    domain=row.get("domain"),
                    query_fingerprint=row.get("query_fingerprint") or "",
                    evidence_id=row.get("evidence_id"),
                    source=row.get("source"),
                    action=row["action"],
                    reason=row["reason"],
                    domain_match=row.get("domain_match"),
                )
            )
            s.commit()
        return True
    except Exception:
        logger.debug("kb_ingest_audit sqlite path failed", exc_info=True)
        return False


def _append_jsonl(row: dict[str, Any]) -> None:
    try:
        from ..config import get_settings
        base = Path(getattr(get_settings(), "data_dir", "data"))
    except Exception:
        base = Path("data")
    path = base / "audit" / "kb_ingest_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _shadow_jsonl_path() -> Path:
    try:
        from ..config import get_settings

        base = Path(getattr(get_settings(), "data_dir", "data"))
    except Exception:
        base = Path("data")
    path = base / "audit" / "kb_relevance_shadow.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def record_relevance_shadow_batch(
    *,
    scores: list[float],
    threshold: float = 0.45,
    project_id: str | None = None,
    domain: str | None = None,
    query: str | None = None,
    topic_only_reject: int = 0,
    both_reject: int = 0,
    shadow_only_reject: int | None = None,
) -> dict[str, Any]:
    """Persist one topicality-shadow batch summary (JSONL + optional audit row).

    Does **not** change ingest accept/reject behaviour — calibration only.
    """
    ordered = sorted(float(s) for s in scores if isinstance(s, (int, float)))
    n = len(ordered)
    would_reject = sum(1 for s in ordered if s < threshold)
    if shadow_only_reject is None:
        shadow_only_reject = max(0, would_reject - int(both_reject or 0))

    def _pct(p: float) -> float | None:
        if not ordered:
            return None
        return ordered[min(n - 1, int(p * n))]

    row: dict[str, Any] = {
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "domain": domain,
        "query_fingerprint": _fingerprint(query, domain),
        "n": n,
        "threshold": threshold,
        "would_reject": would_reject,
        "would_reject_pct": round((would_reject / n * 100.0), 2) if n else 0.0,
        "p10": _pct(0.10),
        "p50": _pct(0.50),
        "p90": _pct(0.90),
        "shadow_only_reject": int(shadow_only_reject or 0),
        "topic_only_reject": int(topic_only_reject or 0),
        "both_reject": int(both_reject or 0),
    }
    try:
        path = _shadow_jsonl_path()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("relevance shadow jsonl write failed", exc_info=True)

    # One discoverable audit row (reason length ≤ 64).
    try:
        reason = f"rej{would_reject}/{n}@t{threshold:.2f}"[:64]
        record_ingest_audit(
            project_id=project_id,
            domain=domain,
            query=query,
            evidence_id=None,
            source="shadow",
            action="shadow",
            reason=reason,
            domain_match=None,
        )
    except Exception:
        logger.debug("relevance shadow audit row skipped", exc_info=True)
    return row


def load_relevance_shadow_stats(*, limit: int = 50) -> dict[str, Any]:
    """Aggregate recent shadow batch summaries from JSONL (newest last)."""
    limit = max(1, min(int(limit or 50), 500))
    rows: list[dict[str, Any]] = []
    try:
        path = _shadow_jsonl_path()
        if path.is_file():
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in lines[-limit:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        logger.debug("relevance shadow stats read failed", exc_info=True)
        rows = []

    n_batches = len(rows)
    total_n = sum(int(r.get("n") or 0) for r in rows)
    total_reject = sum(int(r.get("would_reject") or 0) for r in rows)
    return {
        "batches": n_batches,
        "samples": total_n,
        "would_reject": total_reject,
        "would_reject_pct": round((total_reject / total_n * 100.0), 2) if total_n else 0.0,
        "shadow_only_reject": sum(int(r.get("shadow_only_reject") or 0) for r in rows),
        "topic_only_reject": sum(int(r.get("topic_only_reject") or 0) for r in rows),
        "both_reject": sum(int(r.get("both_reject") or 0) for r in rows),
        "recent": list(reversed(rows[-min(20, n_batches) :])),
    }
