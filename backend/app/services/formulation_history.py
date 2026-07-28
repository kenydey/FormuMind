"""Formulation revision history and diffing.

Formulation work is version-controlled by nature: a recipe is revised dozens of
times, and the question that matters is almost never "what is it now" but "what
changed between v3 and v4, and why". Storing snapshots alone does not answer
that — a reviewer should not have to read two ingredient tables side by side to
find the one component that moved.

So the diff is the point. ``diff_versions`` reports ingredients added, removed
and adjusted, plus the predicted-metric deltas, which is the shape a formulator
actually reasons about.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from ..domain.schemas import Formulation

# Below this a weight change is rounding noise from renormalisation, not an
# edit anyone made.
_WEIGHT_EPSILON = 1e-4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class IngredientChange:
    name: str
    change: str  # added | removed | adjusted
    before_pct: float | None = None
    after_pct: float | None = None
    role: str = ""

    @property
    def delta_pct(self) -> float | None:
        if self.before_pct is None or self.after_pct is None:
            return None
        return round(self.after_pct - self.before_pct, 4)


@dataclass
class VersionDiff:
    from_version: int
    to_version: int
    ingredient_changes: list[IngredientChange] = field(default_factory=list)
    metric_deltas: dict[str, dict] = field(default_factory=dict)
    change_summary: str = ""
    renamed: tuple[str, str] | None = None

    @property
    def topology_changed(self) -> bool:
        """True when the ingredient *set* moved, not merely the ratios.

        Worth calling out separately: re-balancing a recipe and swapping a
        component out are different kinds of change to review.
        """
        return any(c.change in ("added", "removed") for c in self.ingredient_changes)


def _ingredients_by_name(snapshot: dict) -> dict[str, dict]:
    return {
        str(i.get("name")): i
        for i in (snapshot.get("ingredients") or [])
        if i.get("name")
    }


def diff_snapshots(before: dict, after: dict, *, from_version: int = 0, to_version: int = 0) -> VersionDiff:
    """Compare two formulation snapshots."""
    old = _ingredients_by_name(before)
    new = _ingredients_by_name(after)

    changes: list[IngredientChange] = []
    for name in sorted(set(old) | set(new)):
        old_ing, new_ing = old.get(name), new.get(name)
        if old_ing is None:
            changes.append(
                IngredientChange(
                    name=name, change="added",
                    after_pct=float(new_ing.get("weight_pct") or 0.0),
                    role=str(new_ing.get("role") or ""),
                )
            )
        elif new_ing is None:
            changes.append(
                IngredientChange(
                    name=name, change="removed",
                    before_pct=float(old_ing.get("weight_pct") or 0.0),
                    role=str(old_ing.get("role") or ""),
                )
            )
        else:
            before_pct = float(old_ing.get("weight_pct") or 0.0)
            after_pct = float(new_ing.get("weight_pct") or 0.0)
            if abs(after_pct - before_pct) > _WEIGHT_EPSILON:
                changes.append(
                    IngredientChange(
                        name=name, change="adjusted",
                        before_pct=before_pct, after_pct=after_pct,
                        role=str(new_ing.get("role") or ""),
                    )
                )

    metric_deltas: dict[str, dict] = {}
    old_metrics = before.get("predicted") or {}
    new_metrics = after.get("predicted") or {}
    for metric in sorted(set(old_metrics) | set(new_metrics)):
        old_value, new_value = old_metrics.get(metric), new_metrics.get(metric)
        if old_value is None or new_value is None:
            metric_deltas[metric] = {
                "before": old_value, "after": new_value, "delta": None, "pct": None
            }
            continue
        delta = float(new_value) - float(old_value)
        metric_deltas[metric] = {
            "before": round(float(old_value), 4),
            "after": round(float(new_value), 4),
            "delta": round(delta, 4),
            "pct": round(delta / float(old_value) * 100.0, 2) if old_value else None,
        }

    old_name, new_name = str(before.get("name") or ""), str(after.get("name") or "")
    return VersionDiff(
        from_version=from_version,
        to_version=to_version,
        ingredient_changes=changes,
        metric_deltas=metric_deltas,
        renamed=(old_name, new_name) if old_name != new_name else None,
    )


def describe_diff(diff: VersionDiff) -> str:
    """One-line summary, for when no author-written note was supplied."""
    if not diff.ingredient_changes and not diff.renamed:
        return "无成分变化"
    parts: list[str] = []
    for kind, label in (("added", "新增"), ("removed", "移除"), ("adjusted", "调整")):
        names = [c.name for c in diff.ingredient_changes if c.change == kind]
        if names:
            shown = "、".join(names[:3]) + ("…" if len(names) > 3 else "")
            parts.append(f"{label} {len(names)} 项（{shown}）")
    if diff.renamed:
        parts.append(f"重命名：{diff.renamed[0]} → {diff.renamed[1]}")
    return "；".join(parts)


class FormulationHistoryStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_version(
        self,
        formulation: Formulation,
        *,
        lineage_id: str | None = None,
        parent_version_id: str | None = None,
        change_summary: str = "",
        created_by: str = "",
        project_id: str | None = None,
    ):
        """Append a revision. Never mutates an existing row.

        When ``parent_version_id`` is given the lineage and version number are
        derived from it, so a caller only has to say what it branched from.
        """
        from ..db.models import FormulationVersion
        from ..db.session_utils import commit_session

        snapshot = formulation.model_dump(mode="json")
        with commit_session(self._session_factory) as session:
            parent = (
                session.get(FormulationVersion, parent_version_id)
                if parent_version_id
                else None
            )
            if parent is not None:
                lineage = parent.lineage_id
            else:
                lineage = lineage_id or str(uuid.uuid4())

            next_version = (
                session.query(func.coalesce(func.max(FormulationVersion.version), 0))
                .filter(FormulationVersion.lineage_id == lineage)
                .scalar()
                or 0
            ) + 1

            summary = change_summary
            if not summary and parent is not None:
                summary = describe_diff(diff_snapshots(parent.snapshot or {}, snapshot))

            row = FormulationVersion(
                id=str(uuid.uuid4()),
                lineage_id=lineage,
                version=next_version,
                parent_version_id=parent.id if parent is not None else None,
                project_id=project_id,
                name=formulation.name[:255],
                domain=formulation.domain.value,
                snapshot=snapshot,
                change_summary=summary,
                created_by=created_by[:80],
                created_at=_utcnow(),
            )
            session.add(row)
            session.flush()
            session.expunge(row)
            return row

    def get(self, version_id: str):
        from ..db.models import FormulationVersion

        with self._session_factory() as session:
            return session.get(FormulationVersion, version_id)

    def lineage(self, lineage_id: str) -> list:
        from ..db.models import FormulationVersion

        with self._session_factory() as session:
            return (
                session.query(FormulationVersion)
                .filter(FormulationVersion.lineage_id == lineage_id)
                .order_by(FormulationVersion.version)
                .all()
            )

    def latest(self, lineage_id: str):
        versions = self.lineage(lineage_id)
        return versions[-1] if versions else None

    def ancestry(self, version_id: str) -> list:
        """Walk parent links back to the root — the path this revision took.

        With branching, the lineage list is not a single line, so "how did we
        get here" needs the actual chain rather than everything sharing the id.
        """
        from ..db.models import FormulationVersion

        chain: list = []
        seen: set[str] = set()
        with self._session_factory() as session:
            current = session.get(FormulationVersion, version_id)
            while current is not None and current.id not in seen:
                seen.add(current.id)
                chain.append(current)
                if not current.parent_version_id:
                    break
                current = session.get(FormulationVersion, current.parent_version_id)
        return list(reversed(chain))

    def diff_versions(self, from_id: str, to_id: str) -> VersionDiff | None:
        before, after = self.get(from_id), self.get(to_id)
        if before is None or after is None:
            return None
        diff = diff_snapshots(
            before.snapshot or {},
            after.snapshot or {},
            from_version=before.version,
            to_version=after.version,
        )
        diff.change_summary = after.change_summary or describe_diff(diff)
        return diff

    def count(self) -> int:
        from ..db.models import FormulationVersion

        with self._session_factory() as session:
            return int(session.query(func.count(FormulationVersion.id)).scalar() or 0)


_store: FormulationHistoryStore | None = None


def get_history_store() -> FormulationHistoryStore:
    global _store
    if _store is None:
        from ..db.database import default_session_factory

        _store = FormulationHistoryStore(default_session_factory())
    return _store
