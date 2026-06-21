"""Chemist Agent — chemical-compatibility expert (v0.8).

Enforces hard chemical rules with a deterministic core (RDKit functional-group
detection + a carrier-compatibility rule table) and an optional LLM polish of the
human-readable explanation. The verdict and recommendations are fully
reproducible offline; the LLM never changes the decision.

Canonical interception: a water-insoluble blocked isocyanate (e.g. Desmodur BL
3175) introduced into a waterborne system is intercepted, the incompatibility is
explained, and waterborne crosslinker / resin / catalyst alternatives are
recommended.
"""
from __future__ import annotations

from ..domain.knowledge import RAW_MATERIALS
from ..domain.schemas import (
    AgentFinding,
    AgentIssue,
    Formulation,
    Recommendation,
    Requirement,
)
from . import rules


def _rdkit_chem():
    """Return rdkit.Chem or None (graceful when RDKit is not installed)."""
    try:
        from rdkit import Chem  # type: ignore

        return Chem
    except Exception:
        return None


def _is_waterborne(form: Formulation) -> bool:
    """Waterborne system heuristic — matches predictor's >30 wt% water rule."""
    return any(
        i.name == "Deionized water" and i.weight_pct > 30 for i in form.ingredients
    )


def _carrier_of(name: str) -> str:
    """Carrier requirement of a raw material ('aqueous'|'solvent'|'both')."""
    return RAW_MATERIALS.get(name, {}).get("carrier", "both")


def _has_free_isocyanate(smiles: str | None) -> bool:
    """RDKit SMARTS test for a free isocyanate group; False when RDKit absent."""
    Chem = _rdkit_chem()
    if Chem is None or not smiles:
        return False
    mol = Chem.MolFromSmiles(smiles)
    patt = Chem.MolFromSmarts(rules.FREE_ISOCYANATE_SMARTS)
    return bool(mol is not None and patt is not None and mol.HasSubstructMatch(patt))


def _alternatives_for(role: str, offender: str) -> list[Recommendation]:
    """Build waterborne replacement recommendations for an offending ingredient."""
    recs: list[Recommendation] = []
    kind = rules.KIND_BY_ROLE.get(role, "review")
    for alt in rules.WATERBORNE_ALTERNATIVES.get(role, []):
        recs.append(
            Recommendation(
                kind=kind,
                target=offender,
                suggestion=alt,
                rationale=(
                    f"{alt} is water-compatible (carrier={_carrier_of(alt)}); "
                    f"use it in place of solvent-borne {offender}."
                ),
            )
        )
    # When swapping an isocyanate crosslinker, also steer the cure catalyst to a
    # waterborne-friendly bismuth catalyst instead of tin (DBTL).
    if role == "hardener":
        recs.append(
            Recommendation(
                kind="swap_catalyst",
                target=rules.TIN_CATALYST_NAME,
                suggestion=rules.WATERBORNE_CATALYST,
                rationale=(
                    "Waterborne 2K-PU favours a bismuth catalyst; tin (DBTL) "
                    "promotes side reactions with water."
                ),
            )
        )
    return recs


class ChemistAgent:
    """Chemical-compatibility expert agent.

    Deterministic core decides pass/warn/intercept and the recommendations; an
    optional LLM call only rewrites the issue messages for readability.
    """

    name = "chemist"

    def inspect(
        self,
        form: Formulation,
        requirement: Requirement | None = None,
        explain: bool = True,
    ) -> AgentFinding:
        issues: list[AgentIssue] = []
        waterborne = _is_waterborne(form)

        if waterborne:
            for ing in form.ingredients:
                if ing.weight_pct <= 0 or ing.role == "solvent":
                    continue  # the solvent/water itself is the carrier, not carried
                solvent_only = (
                    _carrier_of(ing.name) == "solvent"
                    or ing.name in rules.SOLVENT_ONLY_NAMES
                )
                free_nco = _has_free_isocyanate(ing.smiles)
                if solvent_only or free_nco:
                    code = (
                        "free_isocyanate_in_water"
                        if free_nco
                        else "isocyanate_water_incompatibility"
                    )
                    detail = (
                        "contains free isocyanate (N=C=O) groups that hydrolyse in "
                        "water, releasing CO₂ (foaming) and consuming crosslinker"
                        if free_nco
                        else "is a solvent-borne component that does not disperse in "
                        "a waterborne system"
                    )
                    issues.append(
                        AgentIssue(
                            code=code,
                            severity="high",
                            ingredient=ing.name,
                            message=(
                                f"{ing.name} {detail}; it is incompatible with this "
                                f"waterborne formulation."
                            ),
                            recommendations=_alternatives_for(ing.role, ing.name),
                        )
                    )

        # Reuse the existing acid/base compatibility rule.
        from ..domain import chemistry

        for warning in chemistry.check_acid_base_conflict(form):
            issues.append(
                AgentIssue(
                    code="acid_base_conflict",
                    severity="medium",
                    ingredient=None,
                    message=warning,
                )
            )

        status = (
            "intercept"
            if any(i.severity == "high" for i in issues)
            else "warn"
            if issues
            else "pass"
        )
        engine = "deterministic"

        if explain and issues:
            polished = self._llm_explain(form, issues)
            if polished:
                engine = "deterministic+llm"
                for issue, msg in zip(issues, polished):
                    if msg:
                        issue.message = msg

        return AgentFinding(agent=self.name, status=status, issues=issues, engine=engine)

    def _llm_explain(
        self, form: Formulation, issues: list[AgentIssue]
    ) -> list[str] | None:
        """Optionally rewrite issue messages via the LLM (pure-JSON contract).

        Returns a list of polished messages aligned with ``issues``, or None when
        no LLM is configured / the reply is unusable. Never changes the verdict.
        """
        from ..services import llm as llm_service

        catalog = "; ".join(
            f"[{n}] {i.code}:{i.ingredient or '-'}" for n, i in enumerate(issues)
        )
        prompt = (
            "You are a formulation chemist. A deterministic rule engine flagged "
            "the chemical-compatibility issues below. Rewrite each as a clear, "
            "concise engineering explanation in the same language as the "
            "formulation name; keep the technical facts and DO NOT change the "
            "conclusion.\n"
            f"Formulation: {form.name}\n"
            f"Issues: {catalog}\n"
            'Return JSON only: {"messages": ["...", "..."]} in the same order.'
        )
        data = llm_service.complete_json(prompt)
        if not data or not isinstance(data.get("messages"), list):
            return None
        messages = [str(m) for m in data["messages"]]
        if len(messages) != len(issues):
            return None
        return messages
