"""MetricPrior DSL evaluation (YAML/structured priors)."""
from __future__ import annotations

import logging
from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
from ...domain import features
from ...domain.chemistry import amine_epoxy_ratio
from ...domain.schemas import Formulation, MetricPriorSpec, Requirement

logger = logging.getLogger(__name__)


def _sum_role(form: Formulation, role: str) -> float:
    return sum(i.weight_pct for i in form.ingredients if i.role == role)


def _crosslink_penalty(ratio: float, target: float = 2.0) -> float:
    return max(0.0, 1.0 - abs(ratio - target) / 3.0)


def evaluate_prior_spec(
    spec: MetricPriorSpec,
    form: Formulation,
    process: dict | None,
    req: Requirement | None,
) -> tuple[float | None, str]:
    """Return (value, tier) from a MetricPriorSpec."""
    if spec.prior_yaml:
        try:
            import yaml

            data = yaml.safe_load(spec.prior_yaml) or {}
        except Exception as exc:
            return degrade_return(logger, exc, "operation failed", None), "cold-start"
    else:
        data = {}

    prior = data.get("prior", data)
    if not prior:
        return None, "cold-start"

    value = float(prior.get("intercept", 0.0))
    feats = features.featurize(form, process)
    ratio = amine_epoxy_ratio(form) or 0.0

    for term in prior.get("terms", []):
        coef = float(term.get("coef", 0.0))
        if "role" in term:
            value += coef * _sum_role(form, term["role"])
        elif term.get("derived") == "resin_hardener_ratio":
            if term.get("transform") == "crosslink_penalty":
                target = float(term.get("target", 2.0))
                value += coef * _crosslink_penalty(ratio, target)
            else:
                value += coef * ratio
        elif "derived" in term and term["derived"] in feats:
            value += coef * float(feats[term["derived"]])

    for pt in prior.get("process", []):
        key = pt.get("key")
        if key and process and key in process:
            value += float(pt.get("coef", 0.0)) * float(process[key])

    mol = prior.get("mol_correction") or {}
    if mol.get("enabled"):
        try:
            from ...services.predictor import _molecular_features

            desc = _molecular_features(form)
            if desc:
                corr = 1.0
                corr += float(mol.get("logp", 0.0)) * desc.get("mean_logp", 0.0)
                corr += float(mol.get("tpsa", 0.0)) * (desc.get("mean_tpsa", 0.0) / 100.0)
                corr += float(mol.get("fsp3", 0.0)) * desc.get("mean_fsp3", 0.0)
                value *= corr
        except Exception as exc:
            log_handled_exception(logger, exc, "handled exception")

    conf = spec.confidence or data.get("confidence", "prior")
    tier = "prior" if conf in ("user", "example", "high", "prior") else "role-based"
    return round(value, 4), tier
