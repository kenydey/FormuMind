"""Inspector Agent — regulatory / compliance expert (v0.8).

A thin expert agent over the existing compliance helpers in
``domain/chemistry.py``: it surfaces EU REACH SVHC candidates and VOC-category /
VOC-limit issues as structured findings. Fully deterministic and offline.
"""
from __future__ import annotations

from ..domain.schemas import (
    AgentFinding,
    AgentIssue,
    Formulation,
    Recommendation,
    Requirement,
)


class InspectorAgent:
    """Regulatory reviewer: REACH SVHC + VOC category/limit checks."""

    name = "inspector"

    def inspect(
        self,
        form: Formulation,
        requirement: Requirement | None = None,
        explain: bool = True,
    ) -> AgentFinding:
        from ..domain import chemistry

        issues: list[AgentIssue] = []

        # REACH SVHC candidates (one issue per offending ingredient).
        for name in _svhc_names(form):
            issues.append(
                AgentIssue(
                    code="svhc",
                    severity="medium",
                    ingredient=name,
                    message=(
                        f"{name} is an EU REACH SVHC candidate; confirm regulatory "
                        f"status and authorisation before use."
                    ),
                    recommendations=[
                        Recommendation(
                            kind="review",
                            target=name,
                            suggestion="Verify REACH SVHC / authorisation status",
                            rationale="Listed as a Substance of Very High Concern candidate.",
                        )
                    ],
                )
            )

        # VOC category + limit check (needs a predicted voc_gpl + a limit).
        voc_gpl = form.predicted.get("voc_gpl")
        voc_limit = requirement.voc_limit_gpl if requirement is not None else None
        if voc_gpl is not None and voc_limit is not None and voc_gpl > voc_limit:
            category = chemistry.check_voc_category(form, voc_gpl)
            issues.append(
                AgentIssue(
                    code="voc_exceedance",
                    severity="medium",
                    ingredient=None,
                    message=(
                        f"VOC {voc_gpl:.0f} g/L exceeds the limit {voc_limit:.0f} g/L "
                        f"(category: {category})."
                    ),
                    recommendations=[
                        Recommendation(
                            kind="review",
                            target=None,
                            suggestion="Reduce solvent load or switch to a waterborne carrier",
                            rationale=f"Formulation classified as {category}.",
                        )
                    ],
                )
            )

        status = "warn" if issues else "pass"
        return AgentFinding(
            agent=self.name, status=status, issues=issues, engine="deterministic"
        )


def _svhc_names(form: Formulation) -> list[str]:
    """List SVHC-candidate ingredient names present in the formulation."""
    from ..domain import chemistry
    from ..domain.knowledge import RAW_MATERIALS

    found: list[str] = []
    for ing in form.ingredients:
        if ing.weight_pct <= 0:
            continue
        spec = RAW_MATERIALS.get(ing.name, {})
        if spec.get("svhc") or ing.name in chemistry._SVHC_NAMES:
            found.append(ing.name)
    return found
