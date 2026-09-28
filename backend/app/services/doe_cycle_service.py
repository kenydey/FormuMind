"""
DOE cycle service for closed-loop automation.

Implements the run_doe_cycle task that executes one iteration of the
Bayesian optimization closed-loop:
1. Get candidate formulations from recommendation service (Top-12)
2. Use Baybe engine to generate next experiment points
3. Write experiments to database with pending status
"""
from __future__ import annotations

import logging
from typing import Dict, Any

from ..domain.schemas import Requirement
from ..db.database import default_session_factory
from ..db.session_utils import commit_session
from ..db.models import ExperimentRow

logger = logging.getLogger(__name__)

def run_doe_cycle(requirement: Requirement) -> Dict[str, Any]:
    """
    Execute one DOE cycle for closed-loop automation.
    
    Args:
        requirement: The optimization requirement
        
    Returns:
        Dict with experiment IDs and status
    """
    logger.info(f"Starting DOE cycle for requirement: {requirement.domain}")

    # 1. Get candidate formulations from recommendation service (Top-12)。
    # 注意：RecommendFormulationsRequest 的 n 约束为 le=12；n=20 会恒抛
    # ValidationError 并被吞掉导致恒回退 baseline（P1 B-9）。
    try:
        from ..api.formulations import RecommendFormulationsRequest, recommend_formulations as recommendation_recommend_formulations
        req_obj = RecommendFormulationsRequest(requirement=requirement, n=12)
        rec_result = recommendation_recommend_formulations(req_obj)
        candidate_formulations = rec_result.formulations
        logger.info(f"Got {len(candidate_formulations)} candidate formulations")
    except Exception as e:
        # Fail-open：回退单条 baseline，但必须打 error 级日志，避免静默降级
        # （Top-12 候选从未生效的故障模式）。
        logger.error(f"Failed to get recommendations: {e}")
        # Fallback: use baseline formulation if recommendation fails
        from ..domain import knowledge
        baseline = knowledge.baseline_formulation(requirement)
        candidate_formulations = [baseline] if baseline else []
        logger.warning(f"Using fallback: {len(candidate_formulations)} formulations")
    
    if not candidate_formulations:
        logger.error("No candidate formulations available")
        return {"experiment_ids": [], "status": "error", "message": "No candidates"}
    
    # 2. Use Baybe engine to generate next experiment points
    try:
        from ..services.engines.baybe_engine import BaybeCampaignEngine
        baybe_engine = BaybeCampaignEngine()
        if not baybe_engine.available():
            logger.warning("Baybe engine not available, falling back to LHS")
            # Fallback to LHS DOE generation
            from ..services.active_learning import active_learning_doe
            
            active_result = active_learning_doe(
                req=requirement,
                existing=[],  # No existing experiments for pure generation
                n_suggest=5,
                design="lhs",
                engine="auto",
                workbench_campaign_id=None,
                budget_remaining=None
            )
            
            # Convert DOERun objects to experiment dicts
            experiment_dicts = []
            for run in active_result.plan.runs:
                exp_dict = {
                    "run_id": run.run_id,
                    "coded_factors": run.coded,
                    "natural_factors": run.natural,
                    "ai_suggested": run.ai_suggested,
                    "infeasible": run.infeasible,
                    "infeasible_reason": run.infeasible_reason
                }
                experiment_dicts.append(exp_dict)
                
            logger.info(f"Generated {len(experiment_dicts)} experiments via LHS fallback")
        else:
            # Use Baybe engine
            active_result = baybe_engine.recommend(
                req=requirement,
                measurements=[],  # No existing measurements for generation
                batch_size=5,
                design="baybe_active",
                workbench_campaign_id=None
            )
            
            # Convert Baybe recommendation to experiment dicts
            experiment_dicts = []
            for run in active_result.plan.runs:
                exp_dict = {
                    "run_id": run.run_id,
                    "coded_factors": run.coded,
                    "natural_factors": run.natural,
                    "ai_suggested": run.ai_suggested,
                    "infeasible": run.infeasible,
                    "infeasible_reason": run.infeasible_reason
                }
                experiment_dicts.append(exp_dict)
                
            logger.info(f"Generated {len(experiment_dicts)} experiments via Baybe")
            
    except Exception as e:
        logger.error(f"Failed to generate experiments: {e}")
        return {"experiment_ids": [], "status": "error", "message": f"Generation failed: {e}"}
    
    # 3. Write experiments to database with pending status
    try:
        factory = default_session_factory()
        with commit_session(factory) as session:
            experiment_ids = []
            for exp_dict in experiment_dicts:
                # 只持久化数值型 factors。注意：不要把嵌套 dict（如旧代码的
                # "_doe_metadata"）塞进 ExperimentRow.factors —— 下游消费者
                # 按 dict[str, float] 使用（similarity 里的 `cv > 0` 会直接
                # TypeError；SimilarFormulationMatch 的 factors 字段也会校验
                # 失败），且 db/store 的 _coerce_factor_floats 本就丢弃这类
                # metadata blob，没有任何消费者会读回它。
                base_factors = exp_dict["natural_factors"].copy()
                doe_metadata = {
                    "ai_suggested": exp_dict["ai_suggested"],
                    "infeasible": exp_dict["infeasible"],
                    "infeasible_reason": exp_dict["infeasible_reason"] or "",
                }

                experiment = ExperimentRow(
                    item_id=None,
                    domain=requirement.domain.value,
                    project_id=(requirement.project_id or ""),
                    factors=base_factors,
                    cure_temperature_c=None,
                    measured={},
                    source="lab",
                    label=str(exp_dict["run_id"]),
                )
                session.add(experiment)
                session.flush()  # Get the ID
                logger.debug(
                    "Saved experiment %s doe_metadata=%s",
                    experiment.id,
                    doe_metadata,
                )
                experiment_ids.append(str(experiment.id))
            
            logger.info(f"Saved {len(experiment_ids)} experiments to database")
            

            # W2-6 (P1-2): link each saved experiment (run) to the formulation
            # candidates it was generated from. Fail-open: never break the cycle.
            try:
                from . import provenance as _prov

                _fids = [
                    _prov.formulation_id_for(f)
                    for f in (candidate_formulations or [])[:5]
                ]
                for _eid in experiment_ids:
                    for _fid in _fids:
                        _prov.link("run", _eid, "formulation", _fid, "tests")
            except Exception:
                pass

            return {
                "experiment_ids": experiment_ids,
                "status": "success",
                "count": len(experiment_ids),
                "message": f"Generated {len(experiment_ids)} new experiments"
            }
            
    except Exception as e:
        logger.error(f"Failed to save experiments: {e}")
        return {"experiment_ids": [], "status": "error", "message": f"Save failed: {e}"}
