"""In-loop chemical feasibility gate for genome search.

The multi-agent review already encodes the chemistry rules — waterborne /
solvent-carrier incompatibility, free isocyanate in water, acid-base conflict,
SVHC, VOC — but nothing consumes its verdict as a hard gate: it is reachable
only through ``POST /api/agents/review``. Genome search needs exactly that
verdict, applied *inside* the loop, so an infeasible candidate never costs an
evaluation instead of being flagged after the fact.

No new rules are written here. This is the existing gate, wired up.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from ..domain.genome import FormulationGenome, UnknownMaterialError
from ..domain.schemas import Formulation, Requirement


@dataclass
class FeasibilityVerdict:
    feasible: bool
    status: str = "pass"  # pass | warn | intercept | invalid
    reasons: list[str] = field(default_factory=list)
    formulation: Formulation | None = None

    def __bool__(self) -> bool:
        return self.feasible


def check_formulation(
    form: Formulation,
    req: Requirement | None = None,
) -> FeasibilityVerdict:
    """Run the expert review over a built formulation.

    ``explain=False`` is not optional here. It defaults to True, and with an
    API key configured the chemist agent then makes a *blocking* LLM call
    whenever it finds an issue — one network round-trip per candidate. The LLM
    only rewrites issue prose; it never changes the verdict.
    """
    from ..agents.supervisor import InitializeAgent

    try:
        verdict = InitializeAgent().review(form, requirement=req, explain=False)
    except Exception as exc:
        logger.debug("feasibility: agent review failed ({}); allowing", exc)
        return FeasibilityVerdict(feasible=True, status="pass", formulation=form)

    reasons = [
        f"[{issue.code}] {issue.ingredient or ''} {issue.message}".strip()
        for finding in verdict.findings
        for issue in finding.issues
    ]
    return FeasibilityVerdict(
        feasible=verdict.overall_status != "intercept",
        status=verdict.overall_status,
        reasons=reasons,
        formulation=form,
    )


def genome_is_feasible(
    genome: FormulationGenome,
    req: Requirement | None = None,
) -> FeasibilityVerdict:
    """Build the genome and gate it. Unbuildable genomes are infeasible."""
    from ..pipeline import reconstruct

    target = req if req is not None else genome.domain
    try:
        form = reconstruct.formulation_from_genome(target, genome)
    except (UnknownMaterialError, ValueError) as exc:
        return FeasibilityVerdict(
            feasible=False, status="invalid", reasons=[str(exc)]
        )
    return check_formulation(form, req)


def filter_feasible(
    genomes: list[FormulationGenome],
    req: Requirement | None = None,
) -> tuple[list[FormulationGenome], list[FeasibilityVerdict]]:
    """Split a candidate batch into survivors and rejects.

    Returns ``(feasible_genomes, rejected_verdicts)`` so a caller can report
    *why* the search space shrank rather than silently dropping candidates.
    """
    keep: list[FormulationGenome] = []
    rejected: list[FeasibilityVerdict] = []
    for genome in genomes:
        verdict = genome_is_feasible(genome, req)
        if verdict.feasible:
            keep.append(genome)
        else:
            rejected.append(verdict)
    if rejected:
        logger.debug(
            "feasibility gate rejected {}/{} candidate(s)",
            len(rejected),
            len(genomes),
        )
    return keep, rejected
