"""Session Plan service — structured plans for long tasks (DOE design, multi-round optimization).

An agent submits a structured plan (phases/steps) before running a long task;
the plan must be approved before execution can advance, and earlier phases must
finish before later ones start. Plans persist as JSON under
``data/session_plans/{plan_id}.json`` with a sha256 integrity checksum, in the
same style as ``services/reviewer_fix_loop.py``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

PLAN_PENDING = "pending"
PLAN_APPROVED = "approved"
PLAN_REJECTED = "rejected"
_PLAN_STATUSES = (PLAN_PENDING, PLAN_APPROVED, PLAN_REJECTED)

STEP_PENDING = "pending"
STEP_IN_PROGRESS = "in_progress"
STEP_DONE = "done"
_STEP_STATUSES = (STEP_PENDING, STEP_IN_PROGRESS, STEP_DONE)

_SCHEMA = "1"


def _data_root() -> Path:
    return Path("./data").resolve()


def _plan_path(plan_id: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", plan_id)[:64] or "anon"
    return _data_root() / "session_plans" / f"{safe}.json"


def _utcnow() -> float:
    return time.time()


@dataclass
class PlanStep:
    desc: str
    status: str = STEP_PENDING

    def to_dict(self) -> dict[str, Any]:
        return {"desc": self.desc, "status": self.status}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        return cls(desc=str(data.get("desc", "")), status=str(data.get("status", STEP_PENDING)))


@dataclass
class PlanPhase:
    name: str
    steps: list[PlanStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanPhase":
        return cls(
            name=str(data.get("name", "")),
            steps=[PlanStep.from_dict(s) for s in (data.get("steps") or [])],
        )

    def is_complete(self) -> bool:
        return bool(self.steps) and all(s.status == STEP_DONE for s in self.steps)


@dataclass
class Plan:
    plan_id: str
    session_id: str
    phases: list[PlanPhase] = field(default_factory=list)
    status: str = PLAN_PENDING
    created_at: float = field(default_factory=_utcnow)
    decided_at: float | None = None
    decided_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "phases": [p.to_dict() for p in self.phases],
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        return cls(
            plan_id=str(data.get("plan_id", "")),
            session_id=str(data.get("session_id", "")),
            phases=[PlanPhase.from_dict(p) for p in (data.get("phases") or [])],
            status=str(data.get("status", PLAN_PENDING)),
            created_at=float(data.get("created_at") or 0.0),
            decided_at=data.get("decided_at"),
            decided_by=data.get("decided_by"),
        )


def _checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _save_plan(plan: Plan) -> None:
    path = _plan_path(plan.plan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = plan.to_dict()
    payload["sha256"] = _checksum(payload)
    with _LOCK:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_plan(plan_id: str) -> Plan | None:
    path = _plan_path(plan_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    stored = data.pop("sha256", None)
    if stored != _checksum(data):
        raise ValueError(f"plan file checksum mismatch: {plan_id}")
    return Plan.from_dict(data)


def _validate_phases(phases: Any) -> list[PlanPhase]:
    if not isinstance(phases, list) or not phases:
        raise ValueError("phases must be a non-empty list")
    out: list[PlanPhase] = []
    for i, raw in enumerate(phases):
        if isinstance(raw, PlanPhase):
            out.append(raw)
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"phases[{i}] must be a mapping")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"phases[{i}].name must be a non-empty string")
        steps_raw = raw.get("steps") or []
        if not isinstance(steps_raw, list) or not steps_raw:
            raise ValueError(f"phases[{i}].steps must be a non-empty list")
        steps = []
        for j, s in enumerate(steps_raw):
            desc = s.get("desc") if isinstance(s, dict) else s
            if not isinstance(desc, str) or not desc.strip():
                raise ValueError(f"phases[{i}].steps[{j}] must have a non-empty desc")
            steps.append(PlanStep(desc=desc.strip()))
        out.append(PlanPhase(name=name, steps=steps))
    return out


def submit_plan(session_id: str, phases: list[dict[str, Any]]) -> Plan:
    """Submit a structured plan; returns it in ``pending`` status."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    plan = Plan(
        plan_id=uuid.uuid4().hex[:16],
        session_id=session_id.strip(),
        phases=_validate_phases(phases),
        status=PLAN_PENDING,
    )
    _save_plan(plan)
    logger.info("session plan submitted: %s (session %s)", plan.plan_id, plan.session_id)
    return plan


def get_plan(plan_id: str) -> Plan | None:
    """Load a plan by id; None if it does not exist. Raises on checksum mismatch."""
    return _load_plan(plan_id)


def list_pending_plans() -> list[dict[str, Any]]:
    """Summaries of plans still awaiting approval, oldest first.

    Fail-open per file: unreadable or checksum-mismatched files are skipped
    with a warning instead of aborting the listing.
    """
    root = _data_root() / "session_plans"
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stored = data.pop("sha256", None)
            if stored != _checksum(data):
                raise ValueError(f"plan file checksum mismatch: {path.name}")
            plan = Plan.from_dict(data)
        except Exception:
            logger.warning("session plan list: skipping unreadable file %s", path.name)
            continue
        if plan.status != PLAN_PENDING:
            continue
        items.append(
            {
                "plan_id": plan.plan_id,
                "session_id": plan.session_id,
                "created_at": plan.created_at,
                "phase_names": [p.name for p in plan.phases],
                "step_count": sum(len(p.steps) for p in plan.phases),
            }
        )
    items.sort(key=lambda it: it["created_at"])
    return items


def decide_plan(plan_id: str, approved: bool, *, actor: str | None = None) -> Plan:
    """Approve or reject a plan. Irreversible: deciding twice raises ValueError."""
    plan = _load_plan(plan_id)
    if plan is None:
        raise KeyError(f"plan not found: {plan_id}")
    if plan.status != PLAN_PENDING:
        raise ValueError(f"plan {plan_id} already decided: {plan.status}")
    plan.status = PLAN_APPROVED if approved else PLAN_REJECTED
    plan.decided_at = _utcnow()
    plan.decided_by = actor
    _save_plan(plan)
    logger.info("session plan decided: %s -> %s", plan_id, plan.status)
    return plan


def advance(plan_id: str, phase_name: str, step_idx: int) -> Plan:
    """Mark a step done. Requires an approved plan and all earlier phases complete.

    Idempotent: advancing an already-done step is a no-op.
    """
    plan = _load_plan(plan_id)
    if plan is None:
        raise KeyError(f"plan not found: {plan_id}")
    if plan.status != PLAN_APPROVED:
        raise ValueError(f"plan {plan_id} is not approved (status={plan.status})")
    names = [p.name for p in plan.phases]
    if phase_name not in names:
        raise ValueError(f"unknown phase: {phase_name!r}")
    idx = names.index(phase_name)
    for earlier in plan.phases[:idx]:
        if not earlier.is_complete():
            raise ValueError(
                f"phase {phase_name!r} is blocked: earlier phase {earlier.name!r} is not complete"
            )
    phase = plan.phases[idx]
    if not isinstance(step_idx, int) or not 0 <= step_idx < len(phase.steps):
        raise ValueError(f"step_idx out of range for phase {phase_name!r}: {step_idx!r}")
    step = phase.steps[step_idx]
    if step.status != STEP_DONE:
        step.status = STEP_DONE
        _save_plan(plan)
    return plan
