"""PropertyRegistry — layered metric prediction (mechanistic → prior → ML blend)."""
from __future__ import annotations

from ...domain.project_spec import effective_project_id
from ...domain.schemas import Formulation, Requirement
from .metric_prior import evaluate_prior_spec
from .. import predictor

METRIC_ALIASES: dict[str, str] = {
    "耐盐雾": "salt_spray_hours",
    "salt spray": "salt_spray_hours",
    "salt_spray": "salt_spray_hours",
    "清洗率": "cleaning_efficiency",
    "cost": "cost_cny_per_kg",
    "voc": "voc_gpl",
}


def _resolve_metric_name(metric: str) -> str:
    return METRIC_ALIASES.get(metric.strip().lower(), METRIC_ALIASES.get(metric, metric))


def _generic_role_prior(form: Formulation) -> tuple[float, str]:
    resin = sum(i.weight_pct for i in form.ingredients if i.role == "resin")
    return round(50.0 + resin * 0.5, 3), "role-based"


class PropertyRegistry:
    def predict_all(
        self,
        form: Formulation,
        process: dict | None = None,
        req: Requirement | None = None,
    ) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
        props, std = predictor._predict_mechanistic(form, process)
        tiers: dict[str, str] = {k: "mechanistic" for k in props}

        pid = effective_project_id(req) if req else form.domain.value

        if req:
            for spec in req.metric_priors:
                canon = _resolve_metric_name(spec.metric)
                if spec.builtin_alias:
                    alias_val = props.get(_resolve_metric_name(spec.builtin_alias))
                    if alias_val is not None:
                        props[canon] = alias_val
                        tiers[canon] = "mechanistic"
                        continue
                val, tier = evaluate_prior_spec(spec, form, process, req)
                if val is not None:
                    props[canon] = val
                    tiers[canon] = tier

            for obj in req.objectives or []:
                canon = _resolve_metric_name(obj.metric)
                if canon not in props:
                    val, tier = _generic_role_prior(form)
                    props[canon] = val
                    tiers[canon] = tier

        props, std = predictor._blend_trained(form, process, props, std, project_id=pid)
        from ..training import registry
        from ...domain import features

        vec = features.vector(form, process)
        for metric in props:
            if registry.predict_with_std(form.domain, metric, vec, project_id=pid):
                tiers[metric] = "trained"
            elif registry.predict_with_std(form.domain, metric, vec, project_id=""):
                tiers[metric] = "trained"

        return props, std, tiers


_registry = PropertyRegistry()


def predict_all(
    form: Formulation,
    process: dict | None = None,
    req: Requirement | None = None,
) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    return _registry.predict_all(form, process, req)
