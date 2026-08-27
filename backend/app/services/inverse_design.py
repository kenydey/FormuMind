"""Inverse design: target properties in, formulations out.

Everything upstream runs forward — propose a composition, predict what it
does, score it. Getting from "I need 1000 h salt spray under 250 g/L VOC" to a
composition was unreachable, because the numeric path could only rescale the
ingredients of a fixed template and the LLM path could invent compositions but
never optimise them.

This searches the genome space directly with NSGA-II:

  LLM proposals + baseline + random  →  evolve (crossover, swap, jitter)
                                     →  constraint-dominated non-dominated sort
                                     →  Pareto front of structurally distinct formulations

Why an evolutionary search rather than the Bayesian optimisers already here:

- They are all single-objective. Every one takes ``observe(x, y: float)`` and
  reports ``argmax``; multi-objective collapses to a weighted sum upstream, so
  a genuine trade-off surface cannot survive the scalarisation.
- The decision variables are mixed — *which* material fills a slot is
  categorical, *how much* is continuous. Genetic operators handle that
  natively; a GP over a one-hot expansion does not.
- The evaluation is cheap (~1 ms/candidate: rebuild, predict, gate), so there
  is nothing to gain from a surrogate. The predictor already *is* the
  surrogate; a search over it does not need a second layer.

Hard constraints use constraint-domination rather than a penalty term: a
feasible candidate always beats an infeasible one, and two infeasible ones are
ranked by total violation. That avoids inventing a weight to trade "how much
VOC overshoot equals how much salt spray" — a number nobody can justify.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from loguru import logger

from ..config import Settings, get_settings
from ..domain.genome import FormulationGenome, Slot, candidates_for_role
from ..domain.schemas import (
    DesignCandidate,
    Formulation,
    HardConstraint,
    InverseDesignResult,
    ObjectiveSpec,
    Requirement,
    TargetSpec,
)

# Fraction of a slot's weight used as the mutation step, and the chance a
# swappable slot is re-materialised rather than nudged.
_JITTER_FRACTION = 0.25
_SWAP_PROBABILITY = 0.35
_MUTATION_RATE = 0.3


@dataclass
class _Individual:
    genome: FormulationGenome
    formulation: Formulation | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    violation: float = 0.0
    rank: int = 0
    crowding: float = 0.0

    @property
    def feasible(self) -> bool:
        return self.violation <= 1e-9


# ── evaluation ───────────────────────────────────────────────────────────────


def _total_violation(metrics: dict[str, float], hard: list[HardConstraint]) -> float:
    """Sum of constraint overshoots, normalised so metrics of wildly different
    magnitude (VOC in g/L vs salt spray in hours) contribute comparably."""
    total = 0.0
    for constraint in hard:
        raw = constraint.violation(metrics.get(constraint.metric))
        if raw <= 0:
            continue
        scale = abs(constraint.value) or 1.0
        total += raw / scale
    return total


def _evaluate(
    individual: _Individual,
    req: Requirement,
    hard: list[HardConstraint],
    process: dict,
) -> bool:
    """Build, gate, and predict. False when the candidate is unusable."""
    from ..pipeline import reconstruct
    from ..pipeline.workflow import _score_and_validate
    from .feasibility import check_formulation

    try:
        form = reconstruct.formulation_from_genome(req, individual.genome)
    except Exception:
        return False

    verdict = check_formulation(form, req)
    if not verdict.feasible:
        return False

    # chem_screen stays off: it is network-bound (ChemCrow) and documented as
    # unsuitable for optimisation loops.
    form = _score_and_validate(form, process, req, chem_screen=False)
    individual.formulation = form
    individual.metrics = dict(form.predicted)
    individual.violation = _total_violation(individual.metrics, hard)
    return True


# ── NSGA-II core ─────────────────────────────────────────────────────────────


def _objective_matrix(
    population: list[_Individual], objectives: list[ObjectiveSpec]
) -> list[list[float]]:
    return [
        [float(ind.metrics.get(obj.metric, float("nan"))) for obj in objectives]
        for ind in population
    ]


def _assign_ranks(population: list[_Individual], objectives: list[ObjectiveSpec]) -> None:
    """Constraint-dominated ranking.

    Feasible individuals are sorted by Pareto rank among themselves; every
    infeasible one ranks strictly worse, ordered by violation. This is the
    standard NSGA-II constraint handling and needs no penalty weight.
    """
    from .tradeoff_analysis import compute_pareto_ranks

    feasible = [ind for ind in population if ind.feasible]
    infeasible = [ind for ind in population if not ind.feasible]

    worst = 0
    if feasible:
        ranks = compute_pareto_ranks(_objective_matrix(feasible, objectives), objectives)
        for ind, rank in zip(feasible, ranks):
            ind.rank = rank if rank is not None else 0
            worst = max(worst, ind.rank)
    for offset, ind in enumerate(sorted(infeasible, key=lambda i: i.violation)):
        ind.rank = worst + 1 + offset


def _assign_crowding(population: list[_Individual], objectives: list[ObjectiveSpec]) -> None:
    """Crowding distance within each rank — the diversity pressure that keeps
    the frontier spread out instead of clustering on one attractive corner."""
    by_rank: dict[int, list[_Individual]] = {}
    for ind in population:
        by_rank.setdefault(ind.rank, []).append(ind)

    for group in by_rank.values():
        for ind in group:
            ind.crowding = 0.0
        if len(group) <= 2:
            for ind in group:
                ind.crowding = float("inf")
            continue
        for obj in objectives:
            values = [ind.metrics.get(obj.metric) for ind in group]
            if any(v is None for v in values):
                continue
            order = sorted(range(len(group)), key=lambda i: values[i])
            lo, hi = values[order[0]], values[order[-1]]
            span = (hi - lo) or 1.0
            group[order[0]].crowding = float("inf")
            group[order[-1]].crowding = float("inf")
            for pos in range(1, len(order) - 1):
                prev_v = values[order[pos - 1]]
                next_v = values[order[pos + 1]]
                group[order[pos]].crowding += (next_v - prev_v) / span


def _signature(individual: _Individual) -> tuple[str, ...]:
    return tuple(sorted(individual.genome.materials()))


def _select(population: list[_Individual], n: int, *, per_topology: int = 0) -> list[_Individual]:
    """Elitist selection with structural niching.

    Crowding distance measures spread in *objective* space, so two candidates
    built from entirely different materials look interchangeable to it once
    their predicted metrics are close. Left alone, selection collapses the
    population onto one composition and the answer to "show me structurally
    different options" becomes twenty copies of the same recipe with the
    dial turned slightly.

    Capping how many individuals may share an ingredient set keeps rival
    topologies alive long enough to be optimised on their own terms. The cap
    is a soft one — if distinct topologies run out, the remaining slots are
    filled by normal rank order rather than shrinking the population.
    """
    ordered = sorted(population, key=lambda i: (i.rank, -i.crowding))
    if per_topology <= 0:
        return ordered[:n]

    chosen: list[_Individual] = []
    overflow: list[_Individual] = []
    counts: dict[tuple[str, ...], int] = {}
    for ind in ordered:
        signature = _signature(ind)
        if counts.get(signature, 0) < per_topology:
            counts[signature] = counts.get(signature, 0) + 1
            chosen.append(ind)
        else:
            overflow.append(ind)
        if len(chosen) >= n:
            return chosen[:n]
    return (chosen + overflow)[:n]


# ── genetic operators ────────────────────────────────────────────────────────


def _swappable_indices(genome: FormulationGenome) -> list[int]:
    swappable = {id(s) for s in genome.swappable()}
    return [i for i, slot in enumerate(genome.slots) if id(slot) in swappable]


def _slot_bounds(base: FormulationGenome, req: Requirement) -> dict[str, tuple[float, float]]:
    """Per-role wt% window the search may explore, keyed by role.

    The mechanistic predictor is a set of linear expressions fitted around the
    template compositions; it has no validity domain and will happily
    extrapolate to nonsense (thousands of hours of salt spray) if the search
    wanders far outside them. Bounding the search keeps candidates inside the
    region the surrogate was calibrated for — the alternative is an optimiser
    that "wins" by exploiting its own model.

    Uses the declared DOE lever range where one exists, else ±50% of the
    baseline amount.
    """
    from ..domain.project_spec import resolve_levers
    from ..pipeline import reconstruct

    lever_bounds: dict[str, tuple[float, float]] = {}
    try:
        base_form = reconstruct.formulation_from_genome(req, base)
        for lever in resolve_levers(req, base_form):
            lever_bounds[lever.name] = (float(lever.low), float(lever.high))
    except Exception as exc:
        logger.debug("inverse design: lever bounds unavailable ({})", exc)

    bounds: dict[str, tuple[float, float]] = {}
    for slot in base.slots:
        if slot.material in lever_bounds:
            low, high = lever_bounds[slot.material]
        else:
            current = slot.weight_pct
            low, high = current * 0.5, current * 1.5
        bounds[slot.role] = (max(0.0, low), max(high, low + 0.1))
    return bounds


def _clamp(value: float, role: str, bounds: dict[str, tuple[float, float]]) -> float:
    low, high = bounds.get(role, (0.0, 100.0))
    return round(min(max(value, low), high), 4)


def _dedupe_materials(slots: list[Slot]) -> list[Slot]:
    """Drop slots whose material already appears earlier.

    A swap can land on a material another slot already holds; leaving both
    produces a formulation listing the same ingredient twice, which is
    meaningless chemically and double-counts it in every role sum.
    """
    seen: set[str] = set()
    out: list[Slot] = []
    for slot in slots:
        if slot.material in seen:
            continue
        seen.add(slot.material)
        out.append(slot)
    return out


def _mutate(
    genome: FormulationGenome,
    req: Requirement,
    rng: random.Random,
    pools: dict[str, list[str]],
    bounds: dict[str, tuple[float, float]],
) -> FormulationGenome:
    indices = _swappable_indices(genome)
    if not indices:
        return genome
    slots = [s.model_copy(deep=True) for s in genome.slots]
    for idx in indices:
        if rng.random() > _MUTATION_RATE:
            continue
        slot = slots[idx]
        taken = {s.material for i, s in enumerate(slots) if i != idx}
        pool = [m for m in pools.get(slot.role, []) if m != slot.material and m not in taken]
        if pool and rng.random() < _SWAP_PROBABILITY:
            slot.material = rng.choice(pool)
        else:
            step = max(slot.weight_pct * _JITTER_FRACTION, 0.1)
            slot.weight_pct = _clamp(slot.weight_pct + rng.gauss(0.0, step), slot.role, bounds)
    return genome.model_copy(update={"slots": _dedupe_materials(slots)})


def _crossover(
    a: FormulationGenome, b: FormulationGenome, rng: random.Random
) -> FormulationGenome:
    """Uniform crossover over matching roles.

    Genomes may differ in length or slot order, so pair by role rather than
    index — swapping a hardener slot for a solvent slot produces nonsense.
    """
    b_by_role: dict[str, list[Slot]] = {}
    for slot in b.slots:
        b_by_role.setdefault(slot.role, []).append(slot)

    slots: list[Slot] = []
    for slot in a.slots:
        alternatives = b_by_role.get(slot.role)
        if alternatives and not slot.locked and rng.random() < 0.5:
            slots.append(rng.choice(alternatives).model_copy(deep=True))
        else:
            slots.append(slot.model_copy(deep=True))
    return a.model_copy(update={"slots": slots})


# ── seeding ──────────────────────────────────────────────────────────────────


def _llm_seed_genomes(
    req: Requirement, objectives: list[ObjectiveSpec], n: int
) -> list[FormulationGenome]:
    """LLM proposals as the initial population.

    This is the half the numeric path cannot do: propose a chemically sensible
    *composition* nobody templated. Search then supplies what the LLM cannot —
    convergence. Deliberately skips the recommend pipeline's dedupe/MMR
    truncation, which exists to trim a shortlist and would throw away exactly
    the population diversity a first generation needs.
    """
    from ..domain.formulation_gate import recommended_to_formulation
    from ..domain.genome import genome_from_formulation
    from . import llm as llm_service

    out: list[FormulationGenome] = []
    try:
        response = llm_service.recommend_formulations(req, objectives, [], n=n)
    except Exception as exc:
        logger.debug("inverse design: LLM seeding failed ({}); continuing", exc)
        return out
    for rec in response.formulas:
        try:
            out.append(genome_from_formulation(recommended_to_formulation(rec)))
        except Exception:
            continue
    return out


def _random_genome(
    base: FormulationGenome,
    rng: random.Random,
    pools: dict[str, list[str]],
    bounds: dict[str, tuple[float, float]],
) -> FormulationGenome:
    slots = [s.model_copy(deep=True) for s in base.slots]
    for idx in _swappable_indices(base):
        slot = slots[idx]
        taken = {s.material for i, s in enumerate(slots) if i != idx}
        pool = [m for m in (pools.get(slot.role) or []) if m not in taken]
        if pool:
            slot.material = rng.choice(pool)
        slot.weight_pct = _clamp(slot.weight_pct * rng.uniform(0.6, 1.4), slot.role, bounds)
    return base.model_copy(update={"slots": _dedupe_materials(slots)})


# ── public entry point ───────────────────────────────────────────────────────


def design(
    req: Requirement,
    targets: TargetSpec | None = None,
    *,
    population: int = 48,
    generations: int = 30,
    seed_with_llm: bool = True,
    seed: int = 42,
    progress_cb=None,
    settings: Settings | None = None,
) -> InverseDesignResult:
    """Search the genome space for formulations meeting ``targets``."""
    from ..domain.objective_contract import normalize_objectives
    from ..pipeline import reconstruct
    from ..pipeline.workflow import process_for
    from .tradeoff_analysis import analyze_tradeoffs

    settings = settings or get_settings()
    targets = targets or TargetSpec()
    objectives = targets.soft or normalize_objectives(req)
    hard = list(targets.hard)
    process = process_for(req)
    rng = random.Random(seed)
    warnings: list[str] = []

    base = reconstruct.genome_from_requirement(req)
    bounds = _slot_bounds(base, req)
    # Keep at most an eighth of the population on any single ingredient set so
    # rival topologies survive selection long enough to be optimised.
    per_topology = max(2, population // 8)
    pools = {
        role: candidates_for_role(role, req)
        for role in {slot.role for slot in base.swappable()}
    }
    thin = [r for r, p in pools.items() if len(p) < 2]
    if thin:
        warnings.append(
            "以下角色候选材料不足 2 种，无法换料，仅优化配比：" + "、".join(sorted(thin))
        )

    # ── seed ──
    seeded: list[FormulationGenome] = [base]
    seeded_from = {"baseline": 1, "llm": 0, "random": 0}
    if seed_with_llm:
        llm_genomes = _llm_seed_genomes(req, objectives, max(4, population // 6))
        seeded.extend(llm_genomes)
        seeded_from["llm"] = len(llm_genomes)
    while len(seeded) < population:
        seeded.append(_random_genome(base, rng, pools, bounds))
        seeded_from["random"] += 1

    evaluations = 0
    rejected = 0
    current: list[_Individual] = []
    for genome in seeded:
        ind = _Individual(genome=genome)
        evaluations += 1
        if _evaluate(ind, req, hard, process):
            current.append(ind)
        else:
            rejected += 1

    if not current:
        return InverseDesignResult(
            domain=req.domain.value,
            engine="nsga2-numpy",
            evaluations=evaluations,
            rejected_infeasible=rejected,
            seeded_from=seeded_from,
            warnings=warnings + ["初始种群全部不可行，无法搜索"],
        )

    _assign_ranks(current, objectives)
    _assign_crowding(current, objectives)

    # ── evolve ──
    completed = 0
    for gen in range(generations):
        offspring: list[_Individual] = []
        while len(offspring) < population:
            parent_a = _tournament(current, rng)
            parent_b = _tournament(current, rng)
            child_genome = _mutate(
                _crossover(parent_a.genome, parent_b.genome, rng), req, rng, pools, bounds
            )
            child = _Individual(genome=child_genome)
            evaluations += 1
            if _evaluate(child, req, hard, process):
                offspring.append(child)
            else:
                rejected += 1
            if evaluations > population * (generations + 2) * 3:
                break  # runaway guard: every child infeasible
        merged = current + offspring
        _assign_ranks(merged, objectives)
        _assign_crowding(merged, objectives)
        current = _select(merged, population, per_topology=per_topology)
        completed = gen + 1
        if progress_cb:
            progress_cb((gen + 1) / generations, f"gen {gen + 1}/{generations}")

    # ── report ──
    _assign_ranks(current, objectives)
    _assign_crowding(current, objectives)
    forms = [ind.formulation for ind in current if ind.formulation is not None]
    tradeoff = analyze_tradeoffs(forms, objectives, req=req, settings=settings)

    candidates = [
        DesignCandidate(
            formulation=ind.formulation,
            pareto_rank=ind.rank,
            feasible=ind.feasible,
            violation=round(ind.violation, 6),
            materials=ind.genome.materials(),
        )
        for ind in sorted(current, key=lambda i: (i.rank, -i.crowding))
        if ind.formulation is not None
    ]
    if not any(c.feasible for c in candidates):
        warnings.append("未找到满足全部硬约束的配方，返回违反量最小的候选")

    return InverseDesignResult(
        topic=req.headline(),
        domain=req.domain.value,
        candidates=candidates,
        pareto_frontier_ids=list(tradeoff.pareto_frontier_ids) if tradeoff else [],
        tradeoff=tradeoff,
        generations=completed,
        evaluations=evaluations,
        rejected_infeasible=rejected,
        seeded_from=seeded_from,
        engine="nsga2-numpy",
        warnings=warnings,
    )


def _tournament(population: list[_Individual], rng: random.Random) -> _Individual:
    a, b = rng.choice(population), rng.choice(population)
    if (a.rank, -a.crowding) <= (b.rank, -b.crowding):
        return a
    return b
