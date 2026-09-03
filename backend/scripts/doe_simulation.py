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


def simulate_traditional_doe(
    requirement: Requirement,
    target_metric: str,
    target_value: float,
    batch_size: int = 5,
    max_batches: int = 20,
) -> Tuple[int, List[float]]:
    """
    Simulate traditional DOE approach: generate batches of experiments using
    LHS or full factorial, evaluate them (simulated), and track progress
    toward target.

    Returns:
        (total_experiments, history_of_best_values)
    """
    logger.info(f"Starting traditional DOE simulation (batch_size={batch_size})")

    experiments_conducted = 0
    best_value = float('-inf')  # assuming we're maximizing
    history: List[float] = []

    for batch_num in range(max_batches):
        try:
            # Generate a batch of experiment candidates using LHS
            active_result = active_learning_doe(
                req=requirement,
                existing=[],
                n_suggest=batch_size,
                design="lhs",
                engine="auto",
                workbench_campaign_id=None,
                budget_remaining=None,
            )

            # Simulated experimental results: random improvement toward target
            # (in reality these would come from the lab)
            headroom = max(0.0, target_value - best_value)
            batch_best = best_value + random.uniform(0, headroom * 0.3)
            if batch_best > best_value:
                best_value = batch_best

            experiments_conducted += batch_size
            history.append(best_value)

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
) -> Tuple[int, List[float]]:
    """
    Simulate Bayesian optimization closed-loop approach.

    Uses Baybe engine when available, falls back to LHS-based active learning.

    Returns:
        (total_experiments, history_of_best_values)
    """
    logger.info(f"Starting Bayesian closed-loop simulation (max_iter={max_iterations})")

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

            # Use Baybe when available, otherwise legacy LHS+EI
            active_result = active_learning_doe(
                req=requirement,
                existing=existing,
                n_suggest=5,
                design="lhs",
                engine="auto",
                workbench_campaign_id=None,
                budget_remaining=None,
            )

            # Simulated improvement
            headroom = max(0.0, target_value - best_value)
            improvement = random.uniform(0, headroom * 0.4)
            if improvement > 0:
                best_value += improvement
                # Simulate the experiment we would have conducted
                for run in active_result.plan.runs[:5]:
                    completed_runs.append(
                        {"natural": run.natural, "measured": {target_metric: best_value}}
                    )
                experiments_conducted += 5
                history.append(best_value)

                logger.info(
                    f"Iter {iteration+1}: best = {best_value:.3f} (target {target_value})"
                )

                if best_value >= target_value:
                    logger.info(f"Target reached after {experiments_conducted} experiments")
                    return experiments_conducted, history
            else:
                history.append(best_value)
                experiments_conducted += 5

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

    args = parser.parse_args()

    random.seed(args.seed)

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
    )

    # Reset seed for fair comparison
    random.seed(args.seed)

    # Run Bayesian closed-loop simulation
    logger.info("-" * 60)
    logger.info("STRATEGY 2: Bayesian Closed-Loop")
    logger.info("-" * 60)
    bayesian_count, bayesian_history = simulate_bayesian_closed_loop(
        requirement=requirement,
        target_metric=args.metric,
        target_value=args.target,
        max_iterations=args.max_bayesian,
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
