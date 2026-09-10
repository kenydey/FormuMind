
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
    raw = f"{(query or "").strip().lower()}::{(domain or "").strip().lower()}"
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
