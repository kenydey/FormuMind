"""Organization-level dashboard API for cross-project knowledge reuse."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from ..db.models import Campaign, ExperimentRow, ProjectRow
from ..db.session import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/org", tags=["org"])

@router.get("/dashboard")
def org_dashboard() -> dict:
    with get_db_session() as session:
        total_experiments = session.execute(select(func.count()).select_from(ExperimentRow)).scalar() or 0
        total_campaigns = session.execute(select(func.count()).select_from(Campaign)).scalar() or 0
        total_projects = session.execute(select(func.count()).select_from(ProjectRow)).scalar() or 0
        active_projects = session.execute(
            select(func.count()).select_from(ProjectRow).where(ProjectRow.is_archived == False)
        ).scalar() or 0

        domain_counts = session.execute(
            select(ExperimentRow.domain, func.count()).group_by(ExperimentRow.domain)
        ).all()
        by_domain = {d: c for d, c in domain_counts if d}

        top_performers = []
        all_measured = session.execute(select(ExperimentRow.measured)).scalars().all()
        metric_keys = set()
        for m in all_measured:
            if isinstance(m, dict):
                metric_keys.update(m.keys())
        for metric in list(metric_keys)[:5]:
            best = session.execute(
                select(ExperimentRow, ProjectRow)
                .join(ProjectRow, ExperimentRow.project_id == ProjectRow.id, isouter=True)
                .where(ExperimentRow.measured != None)
                .order_by(ExperimentRow.created_at.desc()).limit(50)
            ).all()
            best_exp, best_proj, best_val = None, None, None
            for exp, proj in best:
                if exp.measured and metric in exp.measured:
                    val = exp.measured[metric]
                    if isinstance(val, (int, float)):
                        if best_val is None or val > best_val:
                            best_val, best_exp, best_proj = val, exp, proj
            if best_exp and best_val is not None:
                factors_preview = ", ".join(f"{k}:{v}" for k, v in list(best_exp.factors.items())[:3]) if best_exp.factors else ""
                top_performers.append({
                    "metric": metric, "value": best_val, "experiment_id": best_exp.id,
                    "project_title": best_proj.title if best_proj else "",
                    "formulation_preview": factors_preview,
                    "measured_at": best_exp.created_at.isoformat() if best_exp.created_at else "",
                })

        all_factors = session.execute(select(ExperimentRow.factors)).scalars().all()
        ingredient_counts: dict[str, dict] = {}
        for f in all_factors:
            if isinstance(f, dict):
                for ing, wt in f.items():
                    ingredient_counts.setdefault(ing, {"count": 0, "total_wt": 0.0})
                    ingredient_counts[ing]["count"] += 1
                    ingredient_counts[ing]["total_wt"] += float(wt) if wt else 0
        ingredient_frequency = sorted([
            {"ingredient_name": name, "experiment_count": data["count"],
             "avg_weight_pct": round(data["total_wt"] / data["count"], 2), "best_result_metric": None}
            for name, data in ingredient_counts.items()
        ], key=lambda x: x["experiment_count"], reverse=True)[:20]

        campaigns = session.execute(select(Campaign)).scalars().all()
        converged_count = sum(1 for c in campaigns if c.loop_history and any(r.get("converged") for r in c.loop_history))
        convergence_rate = round(converged_count / len(campaigns), 2) if campaigns else 0.0
        avg_rounds = 0.0
        if campaigns:
            total_rounds = sum(len(c.loop_history) for c in campaigns if c.loop_history)
            total_with_history = sum(1 for c in campaigns if c.loop_history)
            avg_rounds = round(total_rounds / total_with_history, 1) if total_with_history else 0.0

        week_ago = datetime.utcnow() - timedelta(days=7)
        recent_experiments = session.execute(
            select(func.count()).select_from(ExperimentRow).where(ExperimentRow.created_at >= week_ago)
        ).scalar() or 0
        recent_campaigns = session.execute(
            select(func.count()).select_from(Campaign).where(Campaign.created_at >= week_ago)
        ).scalar() or 0

    return {
        "total_experiments": total_experiments, "total_campaigns": total_campaigns,
        "total_projects": total_projects, "active_projects": active_projects,
        "by_domain": by_domain, "top_performers": top_performers,
        "ingredient_frequency": ingredient_frequency,
        "convergence_rate": convergence_rate, "avg_rounds_to_converge": avg_rounds,
        "recent_activity": {"experiments_added": recent_experiments, "campaigns_created": recent_campaigns},
    }
