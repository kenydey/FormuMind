"""
DOE simulation script for comparing Bayesian optimization vs traditional methods.

Simulates two strategies for achieving a target objective:
1. Traditional full factorial or random search (fixed batch size)
2. Bayesian optimization closed-loop (using the actual Baybe engine)

Reports the number of experiments needed to reach the target.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, '.')

from app.domain.schemas import ProductDomain, Requirement
from app.services.active_learning import active_learning_doe
from app.services.neo4j_kg import is_enabled as neo4j_is_enabled
from app.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _seed_all(seed: int) -> None:
    """Seed every RNG the simulation arms can touch.

    stdlib ``random`` (legacy evaluator), numpy (pydoe LHS designs),
    torch (Botorch recommender). Called before EACH arm: the BayBE
    attempt inside the bayesian arm can consume a network-dependent
    number of draws before falling back to legacy, so a single
    seeding at startup is not enough for reproducibility.
    """
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # pragma: no cover
        pass
    try:
        import torch

        torch.manual_seed(seed)
    except ImportError:  # pragma: no cover
        pass


def synthetic_evaluate(
    natural: Dict[str, float],
    factor_names: List[str],
    centers: Dict[str, float],
    spans: Dict[str, float],
    target_value: float,
) -> float:
    """Deterministic synthetic objective for honest strategy comparison.

    Hidden optimum at ``centers`` (fixed 62% of each factor range); value
    falls off quadratically with normalized distance. The peak
    (1.05 * target) is reachable, so "rounds to target" is meaningful.

    NOTE: the previous ``random.uniform`` "evaluator" drew progress
    independently of the suggested points, so the old bayesian-vs-traditional
    comparison measured the RNG, not the optimizers. Kept behind
    ``--evaluator random`` for backward compatibility only.
    """
    dist2 = sum(
        ((natural[name] - centers[name]) / spans[name]) ** 2 for name in factor_names
    ) / max(len(factor_names), 1)
    return target_value * (1.05 - dist2)


def _synthetic_calibration(factors, target_value: float):
    names = [f.name for f in factors]
    centers = {f.name: f.low + 0.62 * (f.high - f.low) for f in factors}
    spans = {f.name: (f.high - f.low) or 1.0 for f in factors}
    return names, centers, spans


def simulate_traditional_doe(
    requirement: Requirement,
    target_metric: str,
    target_value: float,
    batch_size: int = 5,
    max_batches: int = 20,
    evaluator: str = "synthetic",
    seed: int = 42,
    probe: List[Dict] | None = None,
) -> Tuple[int, List[float]]:
    """Simulate traditional DOE: fresh LHS batch per round, honestly evaluated.

    Each batch is evaluated through the (deterministic synthetic) evaluator —
    never through ``random.uniform`` progress draws. Returns
    (total_experiments, history_of_best_values).

    ``probe`` (A-8 observability): when given, one dict per batch is appended
    with ``{"batch", "engine", "n_suggested", "best"}``.
    """
    from app.domain import doe as doe_engine
    from app.pipeline.workflow import build_doe_factors

    logger.info(f"Starting traditional DOE simulation (batch_size={batch_size})")

    factors = build_doe_factors(requirement)
    names, centers, spans = _synthetic_calibration(factors, target_value)

    experiments_conducted = 0
    best_value = float('-inf')  # assuming we're maximizing
    history: List[float] = []

    for batch_num in range(max_batches):
        try:
            # Fresh LHS batch each round (the old code re-suggested the
            # identical seed-0 batch every round and counted phantom runs).
            matrix = doe_engine.latin_hypercube(
                len(factors), batch_size, seed=seed * 1000 + batch_num
            )
            batch_best = best_value
            for row in matrix:
                natural = {
                    f.name: doe_engine.decode(float(c), f)
                    for f, c in zip(factors, row)
                }
                if evaluator == "synthetic":
                    value = synthetic_evaluate(
                        natural, names, centers, spans, target_value
                    )
                else:  # legacy random progress draw (not optimizer-meaningful)
                    headroom = max(0.0, target_value - best_value)
                    value = best_value + random.uniform(0, headroom * 0.3)
                batch_best = max(batch_best, value)

            if batch_best > best_value:
                best_value = batch_best

            experiments_conducted += batch_size
            history.append(best_value)
            if probe is not None:
                probe.append(
                    {
                        "batch": batch_num + 1,
                        "engine": "lhs",
                        "n_suggested": batch_size,
                        "best": best_value,
                    }
                )

            logger.info(
                f"Batch {batch_num+1}: best = {best_value:.3f} (target {target_value})"
            )

            if best_value >= target_value:
                logger.info(f"Target reached after {experiments_conducted} experiments")
                return experiments_conducted, history

        except Exception as e:
            logger.error(f"Error in batch {batch_num}: {e}")
            break

    logger.warning(
        f"Target not reached after {max_batches} batches ({experiments_conducted} experiments)"
    )
    return experiments_conducted, history


def simulate_bayesian_closed_loop(
    requirement: Requirement,
    target_metric: str,
    target_value: float,
    max_iterations: int = 24,
    evaluator: str = "synthetic",
    seed: int = 42,
    probe: List[Dict] | None = None,
) -> Tuple[int, List[float]]:
    """Simulate Bayesian closed-loop optimization.

    Suggested points are evaluated through the (deterministic synthetic)
    evaluator and fed back per-run — never through ``random.uniform``
    progress draws. Uses BayBE when available, legacy LHS+EI otherwise
    (honest comparison: same evaluator for both arms).

    ``probe`` (A-8 observability): when given, one dict per iteration is
    appended with ``{"iteration", "engine", "n_suggested", "best"}`` — the
    acquisition source actually used (``"baybe"`` or ``"legacy"``).

    Returns:
        (total_experiments, history_of_best_values)
    """
    from app.pipeline.workflow import build_doe_factors

    logger.info(f"Starting Bayesian closed-loop simulation (max_iter={max_iterations})")

    factors = build_doe_factors(requirement)
    names, centers, spans = _synthetic_calibration(factors, target_value)

    experiments_conducted = 0
    best_value = float('-inf')
    history: List[float] = []

    # Build a pool of "completed" experiments that grows each iteration
    completed_runs: List[Dict] = []

    for iteration in range(max_iterations):
        logger.info(f"Iteration {iteration+1}/{max_iterations}")

        try:
            # Convert completed runs to a minimal list-of-dicts that
            # active_learning_doe understands via the legacy path.
            from app.domain.schemas import ExperimentRecord

            existing = []
            for run_dict in completed_runs[-50:]:
                existing.append(
                    ExperimentRecord(
                        domain=requirement.domain,
                        factors=run_dict.get("natural", {}),
                        measured=run_dict.get("measured", {}),
                        source="sim",
                        label=f"sim-{iteration}-{len(existing)}",
                    )
                )

            # Use Baybe when available, otherwise legacy LHS+EI.
            # doe_engine="native": the native LHS generator is seedable and
            # deterministic; the pydoe engine calls pydoe.lhs() without a
            # seed (pydoe>=1.0 then draws from OS entropy), which would
            # break --seed reproducibility of this script. The BayBE
            # attempt above does not use doe_engine at all.
            active_result = active_learning_doe(
                req=requirement,
                existing=existing,
                n_suggest=5,
                design="lhs",
                engine="auto",
                doe_engine="native",
                workbench_campaign_id=None,
                budget_remaining=None,
            )

            # Evaluate the suggested points (honestly: per-run values).
            suggested = active_result.plan.runs[:5]
            if not suggested:
                logger.warning("No suggested runs; stopping loop")
                break
            for run in suggested:
                if evaluator == "synthetic":
                    value = synthetic_evaluate(
                        run.natural, names, centers, spans, target_value
                    )
                else:  # legacy random progress draw (not optimizer-meaningful)
                    headroom = max(0.0, target_value - best_value)
                    value = best_value + random.uniform(0, headroom * 0.4)
                completed_runs.append(
                    {"natural": run.natural, "measured": {target_metric: value}}
                )
                best_value = max(best_value, value)
            experiments_conducted += len(suggested)
            history.append(best_value)
            if probe is not None:
                probe.append(
                    {
                        "iteration": iteration + 1,
                        "engine": getattr(active_result, "engine", "unknown"),
                        "n_suggested": len(suggested),
                        "best": best_value,
                    }
                )

            logger.info(
                f"Iter {iteration+1}: best = {best_value:.3f} (target {target_value})"
            )

            if best_value >= target_value:
                logger.info(f"Target reached after {experiments_conducted} experiments")
                return experiments_conducted, history

        except Exception as e:
            logger.error(f"Error in Bayesian iter {iteration}: {e}")
            break

    logger.warning(
        f"Target not reached after {max_iterations} iterations ({experiments_conducted} experiments)"
    )
    return experiments_conducted, history


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare DOE strategies for target achievement")
    parser.add_argument("--target", type=float, default=95.0, help="Target salt spray hours")
    parser.add_argument("--metric", type=str, default="salt_spray_hours", help="Target metric")
    parser.add_argument("--domain", type=str, default="anticorrosion_coating", help="Product domain")
    parser.add_argument("--traditional-batch", type=int, default=5)
    parser.add_argument("--max-traditional", type=int, default=20)
    parser.add_argument("--max-bayesian", type=int, default=24)
    parser.add_argument("--output", type=str, help="Output file for results (JSON)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument(
        "--evaluator",
        type=str,
        default="synthetic",
        choices=["synthetic", "random"],
        help="synthetic: deterministic truth function (default, optimizer-meaningful); "
        "random: legacy random-uniform progress draws (not optimizer-meaningful)",
    )

    args = parser.parse_args()

    _seed_all(args.seed)

    requirement = Requirement(
        domain=ProductDomain(args.domain),
        project_id=f"sim-{args.seed}",
    )

    logger.info("=" * 60)
    logger.info("DOE STRATEGY COMPARISON SIMULATION")
    logger.info("=" * 60)
    logger.info(f"Target: {args.metric} >= {args.target}")
    logger.info(f"Domain: {args.domain}")
    logger.info(f"Neo4j KG enabled: {neo4j_is_enabled()}")
    logger.info(f"Settings optimize_iterations: {get_settings().optimize_iterations}")

    # Run traditional DOE simulation
    logger.info("-" * 60)
    logger.info("STRATEGY 1: Traditional DOE (LHS, fixed batch)")
    logger.info("-" * 60)
    traditional_count, traditional_history = simulate_traditional_doe(
        requirement=requirement,
        target_metric=args.metric,
        target_value=args.target,
        batch_size=args.traditional_batch,
        max_batches=args.max_traditional,
        evaluator=args.evaluator,
        seed=args.seed,
    )

    # Reset seed for fair comparison
    _seed_all(args.seed)

    # Run Bayesian closed-loop simulation
    logger.info("-" * 60)
    logger.info("STRATEGY 2: Bayesian Closed-Loop")
    logger.info("-" * 60)
    bayesian_count, bayesian_history = simulate_bayesian_closed_loop(
        requirement=requirement,
        target_metric=args.metric,
        target_value=args.target,
        max_iterations=args.max_bayesian,
        evaluator=args.evaluator,
        seed=args.seed,
    )

    # Calculate improvement
    if traditional_count > 0 and bayesian_count > 0:
        improvement_pct = ((traditional_count - bayesian_count) / traditional_count) * 100
        improvement_str = f"{improvement_pct:.1f}% fewer experiments"
    elif traditional_count == 0 and bayesian_count > 0:
        improvement_str = "Traditional failed, Bayesian succeeded"
    elif bayesian_count == 0 and traditional_count > 0:
        improvement_str = "Bayesian failed, traditional succeeded"
    else:
        improvement_str = "Both failed to reach target"

    results = {
        "target": {
            "metric": args.metric,
            "value": args.target,
            "domain": args.domain,
        },
        "traditional_doe": {
            "experiments_to_target": traditional_count if traditional_count > 0 else None,
            "achieved": traditional_count > 0
            and bool(traditional_history)
            and traditional_history[-1] >= args.target,
            "history": traditional_history,
            "batches": args.traditional_batch,
            "max_batches": args.max_traditional,
        },
        "bayesian_closed_loop": {
            "experiments_to_target": bayesian_count if bayesian_count > 0 else None,
            "achieved": bayesian_count > 0
            and bool(bayesian_history)
            and bayesian_history[-1] >= args.target,
            "history": bayesian_history,
            "iterations": len(bayesian_history),
            "max_iterations": args.max_bayesian,
        },
        "comparison": {
            "improvement": improvement_str,
            "traditional_experiments": traditional_count,
            "bayesian_experiments": bayesian_count,
            "evaluator": args.evaluator,
        },
        "neo4j_kg_enabled": neo4j_is_enabled(),
    }

    # Print summary
    logger.info("=" * 60)
    logger.info("SIMULATION RESULTS")
    logger.info("=" * 60)
    trad_msg = (
        f"{traditional_count} experiments to target"
        if traditional_count > 0
        else "Failed to reach target"
    )
    bay_msg = (
        f"{bayesian_count} experiments to target"
        if bayesian_count > 0
        else "Failed to reach target"
    )
    logger.info(f"Traditional DOE:    {trad_msg}")
    logger.info(f"Bayesian Closed-Loop: {bay_msg}")
    logger.info(f"Improvement:          {improvement_str}")

    if args.output:
        # JSON cannot represent -inf / inf / NaN; replace with None for portability
        import math
        def _scrub(obj):
            if isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            if isinstance(obj, dict):
                return {k: _scrub(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_scrub(v) for v in obj]
            return obj
        results = _scrub(results)
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Results saved to {args.output}")

    return 0 if (traditional_count > 0 or bayesian_count > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
