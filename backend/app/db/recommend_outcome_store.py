"""Recommendation adopt-outcome telemetry (C-8 weak-success layer).

``register_round`` records every recommendation round as ``adopted=False``
(the denominator); ``record_adopt`` flips a round to adopted on an adopt
signal (idempotent upserts — repeated adopts update instead of
duplicating). ``outcome_stats`` aggregates adoption counts/rates for
``GET /api/ops/recommend-stats`` and is fail-open: when the
``recommend_outcomes`` table does not exist yet (migration not applied) it
returns empty stats instead of raising.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .models import RecommendOutcomeRow

_ADOPT_SIGNALS = ("button", "copied", "campaign")


def _utcnow() -> datetime:
    # Naive UTC, matching dispatcher.py: SQLite returns naive datetimes, so
    # Python-side comparisons (e.g. the 7-day window slice) must be naive too.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_adopt_signal(signal: str) -> str:
    if signal not in _ADOPT_SIGNALS:
        raise ValueError(
            f"adopt_signal must be one of {list(_ADOPT_SIGNALS)}, got {signal!r}"
        )
    return signal


def _snapshot_fingerprint(snapshot: dict | None) -> str | None:
    """Stable component fingerprint reserved for C-5 measurement correlation."""
    if not snapshot:
        return None
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def register_round(
    session: Session,
    *,
    recommend_id: str,
    project_id: str | None = None,
) -> RecommendOutcomeRow:
    """Register one recommendation round as not-yet-adopted (the denominator).

    Idempotent: if the row already exists (e.g. an adopt raced the
    registration), the existing row is returned untouched. Best-effort —
    callers must never let telemetry break the recommendation path.
    """
    row = session.execute(
        select(RecommendOutcomeRow).where(
            RecommendOutcomeRow.recommend_id == recommend_id
        )
    ).scalar_one_or_none()
    if row is None:
        row = RecommendOutcomeRow(
            recommend_id=recommend_id,
            project_id=project_id or None,
            adopted=False,
            created_at=_utcnow(),
        )
        session.add(row)
        session.flush()
    return row


def record_adopt(
    session: Session,
    *,
    recommend_id: str,
    project_id: str | None = None,
    adopt_signal: str = "button",
    formula_snapshot: dict | None = None,
    formula_hash: str | None = None,
) -> RecommendOutcomeRow:
    """Record (or refresh) the adopt signal for one recommendation round.

    ``formula_hash`` defaults to the SHA-256 fingerprint of
    ``formula_snapshot`` when not given explicitly. Adopting a round that was
    never registered (e.g. the round registration was skipped) still works —
    the row is created with ``adopted=True``.
    """
    validate_adopt_signal(adopt_signal)
    row = session.execute(
        select(RecommendOutcomeRow).where(
            RecommendOutcomeRow.recommend_id == recommend_id
        )
    ).scalar_one_or_none()
    if row is None:
        row = RecommendOutcomeRow(
            recommend_id=recommend_id,
            project_id=project_id or None,
            adopted=True,
            adopt_signal=adopt_signal,
            formula_snapshot=formula_snapshot or {},
            formula_hash=(
                formula_hash
                if formula_hash is not None
                else _snapshot_fingerprint(formula_snapshot)
            ),
            created_at=_utcnow(),
        )
        session.add(row)
    else:
        row.adopted = True
        row.adopt_signal = adopt_signal
        if formula_snapshot:
            row.formula_snapshot = formula_snapshot
            row.formula_hash = _snapshot_fingerprint(formula_snapshot)
        elif formula_hash:
            row.formula_hash = formula_hash
    session.flush()
    return row


def outcome_stats(
    session: Session,
    *,
    project_id: str | None = None,
    days: int = 30,
) -> dict:
    """Aggregate adoption stats; fail-open when the table is missing."""
    empty = {
        "total": 0,
        "adopted": 0,
        "adopt_rate": 0.0,
        "by_signal": {},
        "window_days": days,
        "validated": None,
        "validated_note": "实验验证层待 C-5（Datalab 回灌链路）；当前仅统计用户采纳",
    }
    try:
        q = select(RecommendOutcomeRow)
        if project_id is not None:
            q = q.where(RecommendOutcomeRow.project_id == project_id)
        since = _utcnow() - timedelta(days=days)
        q = q.where(RecommendOutcomeRow.created_at >= since)
        rows = session.execute(q).all()
    except OperationalError:
        # Table missing (migration 0038 not applied) — fail open.
        return {**empty, "available": False}
    rows = [r[0] for r in rows]
    total = len(rows)
    adopted = sum(1 for r in rows if r.adopted)
    by_signal: dict[str, int] = {}
    for r in rows:
        by_signal[r.adopt_signal or "button"] = by_signal.get(r.adopt_signal or "button", 0) + 1
    # 7-day window slice for the short-term rate.
    week_ago = _utcnow() - timedelta(days=7)
    week_rows = [r for r in rows if r.created_at and r.created_at >= week_ago]
    return {
        **empty,
        "total": total,
        "adopted": adopted,
        "adopt_rate": round(adopted / total, 4) if total else 0.0,
        "by_signal": by_signal,
        "adopted_7d": sum(1 for r in week_rows if r.adopted),
        "available": True,
        # Strong-success layer (experiment validation) is pending C-5:
        # always null by contract, never fabricated.
        "validated": None,
    }


def recent_adopted(session: Session, *, limit: int = 20) -> list[dict]:
    """Newest adopted rows for the monthly sampling report (C-8 §6)."""
    try:
        rows = (
            session.execute(
                select(RecommendOutcomeRow)
                .where(RecommendOutcomeRow.adopted.is_(True))
                .order_by(RecommendOutcomeRow.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    except OperationalError:
        return []
    return [
        {
            "recommend_id": r.recommend_id,
            "project_id": r.project_id,
            "adopt_signal": r.adopt_signal,
            "formula_snapshot": r.formula_snapshot,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
