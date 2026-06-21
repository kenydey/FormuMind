"""Initialize Agent — the supervisor / orchestrator (v0.8).

Dispatches a formulation to the expert agents (Chemist, Inspector), aggregates
their findings into a single pure-JSON ``ReviewVerdict`` (overall status = the
worst of all findings, with merged & de-duplicated recommendations), and emits
lifecycle events on the reserved Redis bus (a no-op when the bus is disabled).
"""
from __future__ import annotations

from ..domain.schemas import (
    AgentFinding,
    Formulation,
    Recommendation,
    Requirement,
    ReviewVerdict,
)
from . import bus
from .base import worst_status
from .chemist import ChemistAgent
from .inspector import InspectorAgent


def _dedupe(recs: list[Recommendation]) -> list[Recommendation]:
    """Drop duplicate recommendations, preserving first-seen order."""
    seen: set[tuple[str, str | None, str]] = set()
    out: list[Recommendation] = []
    for r in recs:
        key = (r.kind, r.target, r.suggestion)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


class InitializeAgent:
    """Supervisor agent: dispatch → aggregate → publish."""

    name = "initialize"

    def __init__(self) -> None:
        # Expert roster — extend here to add new experts.
        self.experts = [ChemistAgent(), InspectorAgent()]

    def review(
        self,
        form: Formulation,
        requirement: Requirement | None = None,
        explain: bool = True,
    ) -> ReviewVerdict:
        bus.publish("agent_events", {"event": "review_start", "formulation": form.name})

        findings: list[AgentFinding] = [
            expert.inspect(form, requirement=requirement, explain=explain)
            for expert in self.experts
        ]

        overall = worst_status([f.status for f in findings])
        recommendations = _dedupe(
            [rec for f in findings for issue in f.issues for rec in issue.recommendations]
        )
        engine = (
            "deterministic+llm"
            if any(f.engine.endswith("llm") for f in findings)
            else "deterministic"
        )

        verdict = ReviewVerdict(
            formulation_name=form.name,
            overall_status=overall,
            findings=findings,
            recommendations=recommendations,
            engine=engine,
        )
        bus.publish(
            "agent_events",
            {"event": "review_done", "formulation": form.name, "status": overall},
        )
        return verdict
