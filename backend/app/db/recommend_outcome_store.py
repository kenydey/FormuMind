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


# P2: 信号强度 —— 后到的弱信号不得覆盖先到的强信号
# （copied 复制 ≠ button 明确采纳）。
_ADOPT_SIGNAL_RANK = {"copied": 1, "button": 2, "campaign": 3}


def _signal_rank(signal: str | None) -> int:
    return _ADOPT_SIGNAL_RANK.get((signal or "").strip().lower(), 0)


def _snapshot_fingerprint(snapshot: dict | None) -> str | None:
    """Stable component fingerprint reserved for C-5 measurement correlation."""
    if not snapshot:
        return None
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_outcome(session: Session, *, recommend_id: str) -> RecommendOutcomeRow | None:
    """P1-10: 按 recommend_id 取 outcome 行（前端徽标水合）。"""
    return session.execute(
        select(RecommendOutcomeRow).where(
            RecommendOutcomeRow.recommend_id == recommend_id
        )
    ).scalar_one_or_none()


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
        # P2: 弱信号不覆盖强信号 —— copied 后到不得把 button 降级。
        # 风险2 修正：快照/hash 同样受强度门控，否则弱信号会用另一张卡片
        # 的快照污染强信号行（信号 button + 快照 B 的不一致状态）。
        if _signal_rank(adopt_signal) >= _signal_rank(row.adopt_signal):
            row.adopt_signal = adopt_signal
            if formula_snapshot:
                row.formula_snapshot = formula_snapshot
                row.formula_hash = _snapshot_fingerprint(formula_snapshot)
            elif formula_hash:
                row.formula_hash = formula_hash
    session.flush()
    return row


# U-4: 实验验证（强成功层）的启发式关联阈值（召回式指标见下）。
_VALIDATION_MATCH_THRESHOLD = 0.8


def _reverse_material_aliases(snapshot: dict | None) -> dict[str, str]:
    """P1-2: U-5 重命名后的成分名 -> lever 名反向映射。

    adopt 快照里 ingredient 名可能是 material_map 的替换目标名
    （如"树脂A"→"聚氨酯树脂X"），而实验行 factors 键仍是 lever 原名。
    前端在 formula_snapshot 里附带 ``material_map``（lever 名 ->
    {水平: 目标成分名}），此处反查回 lever 名再参与匹配。
    """
    aliases: dict[str, str] = {}
    if not isinstance(snapshot, dict):
        return aliases
    mmap = snapshot.get("material_map")
    if isinstance(mmap, dict):
        for lever_name, level_map in mmap.items():
            if not isinstance(level_map, dict):
                continue
            for _level, target in level_map.items():
                t = str(target).strip() if target else ""
                if t:
                    aliases[t] = str(lever_name).strip()
    return aliases


def _ingredient_names(snapshot: dict | None) -> set[str]:
    """Normalized ingredient name set from an adopt formula_snapshot."""
    names: set[str] = set()
    if not isinstance(snapshot, dict):
        return names
    ings = snapshot.get("ingredients")
    if isinstance(ings, list):
        for ing in ings:
            if isinstance(ing, dict) and ing.get("name"):
                names.add(str(ing["name"]).strip())
            elif isinstance(ing, str) and ing.strip():
                names.add(ing.strip())
    return {n for n in names if n}


def try_mark_experiment_validated(
    session: Session,
    *,
    project_id: str | None,
    factors: dict | None,
    experiment_id: int,
    measured_keys: list[str] | None = None,
) -> str | None:
    """U-4: 强成功层回写 —— 被采纳配方产出 lab 测量时标记实验验证。

    启发式关联：实验行 ``factors`` 的成分名集合（去掉工艺 lever）与
    adopt 快照的成分名集合做**召回式**匹配 ``|F∩S|/|F|``，≥ 0.8 即命中
    （factors 是 lever 名子集，Jaccard 在真实数据下系统性偏低）。
    快照成分名先经 ``material_map`` 反向别名归一（U-5 重命名）。
    只考虑同 project、已 adopted、未验证的行；命中多行时取分值最高的一行。

    返回命中的 ``recommend_id``，无命中返回 None。Fail-open：任何异常
    返回 None，不抛错（调用方在 sync-datalab 成功路径调用，不得把同步
    搞成 500）。
    """
    try:
        return _try_mark_experiment_validated(
            session,
            project_id=project_id,
            factors=factors,
            experiment_id=experiment_id,
            measured_keys=measured_keys,
        )
    except Exception:  # noqa: BLE001 - fail-open contract
        return None


def _try_mark_experiment_validated(
    session: Session,
    *,
    project_id: str | None,
    factors: dict | None,
    experiment_id: int,
    measured_keys: list[str] | None,
) -> str | None:
    from ..domain.levers import is_process_lever

    factor_names = {
        str(k).strip()
        for k in (factors or {})
        if str(k).strip() and not is_process_lever(str(k))
    }
    if not factor_names:
        return None
    q = select(RecommendOutcomeRow).where(
        RecommendOutcomeRow.adopted.is_(True),
        RecommendOutcomeRow.experiment_validated.is_(False),
    )
    if project_id:
        q = q.where(RecommendOutcomeRow.project_id == project_id)
    best: RecommendOutcomeRow | None = None
    best_score = 0.0
    best_overlap: set[str] = set()
    for (row,) in session.execute(q).all():
        snap = row.formula_snapshot
        snap_names = _ingredient_names(snap)
        if not snap_names:
            continue
        # P1-2: U-5 别名归一 —— 快照里的替换目标名映回 lever 名。
        aliases = _reverse_material_aliases(snap)
        if aliases:
            snap_names = {aliases.get(n, n) for n in snap_names}
        overlap = factor_names & snap_names
        # P1-2: 召回式指标 |F∩S|/|F| —— 实验行 factors 是 lever 名子集
        # （F⊆S），Jaccard 在真实数据下系统性够不上 0.8。
        score = len(overlap) / len(factor_names) if factor_names else 0.0
        if score >= _VALIDATION_MATCH_THRESHOLD and score > best_score:
            best, best_score, best_overlap = row, score, overlap
    if best is None:
        return None
    best.experiment_validated = True
    best.validated_at = _utcnow()
    best.validation_summary = {
        "experiment_id": experiment_id,
        "matched_ingredients": sorted(best_overlap),
        "match_score": round(best_score, 3),
        "measured_keys": list(measured_keys or []),
    }
    session.flush()
    return best.recommend_id


def try_validate_adopt_against_lab(
    session: Session,
    *,
    recommend_id: str,
    project_id: str | None,
    snapshot: dict | None,
) -> bool:
    """P2: 先 sync、后 adopt 的补记 —— 采纳发生时，项目里已有 lab 测量
    （measurements 明细表有行）的实验，若与本次采纳快照成分匹配
    （召回式 ≥ 0.8，含 U-5 别名归一），直接标记 experiment_validated。

    Fail-open：任何异常返回 False，不抛错（adopt 主路径不得被遥测搞成 500）。
    """
    try:
        return _try_validate_adopt_against_lab(
            session,
            recommend_id=recommend_id,
            project_id=project_id,
            snapshot=snapshot,
        )
    except Exception:  # noqa: BLE001 - fail-open contract
        return False


def _try_validate_adopt_against_lab(
    session: Session,
    *,
    recommend_id: str,
    project_id: str | None,
    snapshot: dict | None,
) -> bool:
    from ..domain.levers import is_process_lever
    from .models import ExperimentRow, MeasurementRow

    row = get_outcome(session, recommend_id=recommend_id)
    if row is None or not row.adopted or bool(
        getattr(row, "experiment_validated", False)
    ):
        return False
    aliases = _reverse_material_aliases(snapshot)
    snap_names = _ingredient_names(snapshot)
    if aliases:
        snap_names = {aliases.get(n, n) for n in snap_names}
    if not snap_names:
        return False
    # 有 lab 测量明细的实验行（sync-datalab / 手工录入都会写 measurements 表）。
    measured_ids = select(MeasurementRow.experiment_id).distinct()
    q = select(ExperimentRow).where(ExperimentRow.id.in_(measured_ids))
    if project_id:
        q = q.where(ExperimentRow.project_id == project_id)
    best_exp_id: int | None = None
    best_score = 0.0
    best_overlap: set[str] = set()
    for exp in session.execute(q).scalars().all():
        factor_names = {
            str(k).strip()
            for k in (exp.factors or {})
            if str(k).strip() and not is_process_lever(str(k))
        }
        if not factor_names:
            continue
        overlap = factor_names & snap_names
        score = len(overlap) / len(factor_names)
        if score >= _VALIDATION_MATCH_THRESHOLD and score > best_score:
            best_exp_id, best_score, best_overlap = exp.id, score, overlap
    if best_exp_id is None:
        return False
    row.experiment_validated = True
    row.validated_at = _utcnow()
    row.validation_summary = {
        "experiment_id": best_exp_id,
        "matched_ingredients": sorted(best_overlap),
        "match_score": round(best_score, 3),
        "late_adopt": True,  # adopt 发生在 sync 之后，补记
    }
    session.flush()
    return True


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
        "validated": 0,
        "validated_note": "U-4：被采纳配方经 sync-datalab 同步出 lab 测量即计实验验证",
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
        # U-4 强成功层：被采纳且已有 lab 测量回灌的配方数。
        "validated": sum(1 for r in rows if r.adopted and r.experiment_validated),
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
