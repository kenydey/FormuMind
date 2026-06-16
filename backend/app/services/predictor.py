"""Property prediction service (empirical surrogate + trained-model blend).

Predicts performance properties for metal surface treatment formulations.
Also computes economic & sustainability metrics (cost, VOC, sustainability
index) from the raw-material price/VOC metadata in the knowledge base.

The default implementation is a transparent empirical surrogate; trained
models are blended in automatically as lab data accumulates (see training.py).
Each metric is returned together with a ``predicted_std`` uncertainty estimate.
"""
from __future__ import annotations

from ..domain import features, knowledge
from ..domain.chemistry import amine_epoxy_ratio
from ..domain.schemas import Formulation, ProductDomain


def _sum_role(form: Formulation, role: str) -> float:
    return sum(i.weight_pct for i in form.ingredients if i.role == role)


def _has_waterborne(form: Formulation) -> bool:
    return any(i.name == "Deionized water" and i.weight_pct > 30 for i in form.ingredients)


def _molecular_features(form: Formulation) -> dict[str, float]:
    """Optional RDKit-derived descriptors; empty dict when RDKit is absent."""
    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import Descriptors  # type: ignore
    except Exception:
        return {}
    weighted_logp, total = 0.0, 0.0
    for ing in form.ingredients:
        if ing.smiles:
            mol = Chem.MolFromSmiles(ing.smiles)
            if mol is not None:
                weighted_logp += Descriptors.MolLogP(mol) * ing.weight_pct
                total += ing.weight_pct
    return {"mean_logp": weighted_logp / total} if total else {}


def _molformer_available() -> bool:
    """Reserved hook for IBM MoLFormer embeddings (heavy GPU path)."""
    return False


def _molformer_features(form: Formulation) -> dict[str, float]:  # pragma: no cover - reserved
    """Reserved: MoLFormer SMILES-embedding features for richer prediction.

    Intentionally a no-op for now. When wired up, this would load the IBM
    MoLFormer checkpoint (``transformers``, GPU recommended; declared in the
    ``heavy`` extra) and return pooled-embedding descriptors per formulation to
    augment ``_molecular_features``. Returns ``{}`` until the engine is enabled,
    so the empirical surrogate is unaffected.
    """
    if not _molformer_available():
        return {}
    # TODO: heavy GPU path — load MoLFormer, embed ingredient SMILES, pool.
    return {}


def _mixture_density_kgL(form: Formulation) -> float:
    """Mass-weighted liquid density (kg/L) for VOC concentration.

    Uses Caleb Bell's ``thermo`` when installed to look up component densities
    by name; otherwise returns the nominal 1.3 kg/L assumption used previously,
    so offline behaviour is unchanged.
    """
    try:  # pragma: no cover - requires thermo
        from thermo import Chemical

        inv_rho, total = 0.0, 0.0
        for ing in form.ingredients:
            try:
                rho = Chemical(ing.name).rho  # kg/m^3 at ambient
            except Exception:
                rho = None
            if rho and rho > 0:
                frac = ing.weight_pct / 100.0
                inv_rho += frac / (rho / 1000.0)  # kg/L
                total += frac
        if total > 0.5 and inv_rho > 0:
            return float(total / inv_rho)
    except Exception:
        pass
    return 1.3


def _cost_and_sustainability(form: Formulation, voc_limit: float = 420.0) -> dict[str, float]:
    """Compute cost (CNY/kg), VOC (g/L, density ~1.3), sustainability index."""
    cost = 0.0
    voc_mass_frac = 0.0
    for ing in form.ingredients:
        spec = knowledge.RAW_MATERIALS.get(ing.name, {})
        price = spec.get("price_cny_per_kg", 15.0)
        voc_c = spec.get("voc_contrib", 0.0)
        frac = ing.weight_pct / 100.0
        cost += price * frac
        voc_mass_frac += voc_c * frac
    # Density grounds the mass-fraction VOC into g/L. thermo computes a real
    # mass-weighted density when installed; otherwise the 1.3 kg/L nominal.
    density_kgL = _mixture_density_kgL(form)
    voc_gpl = round(voc_mass_frac * density_kgL * 1000, 1)
    # Sustainability index (0=bad, 100=best): penalise high VOC and high cost.
    voc_penalty = min(50.0, (voc_gpl / max(voc_limit, 1)) * 50.0)
    cost_penalty = min(50.0, (cost / 50.0) * 50.0)  # 50 CNY/kg = full penalty
    sustainability_idx = round(max(0.0, 100.0 - voc_penalty - cost_penalty), 1)
    return {
        "cost_cny_per_kg": round(cost, 2),
        "voc_gpl": voc_gpl,
        "sustainability_idx": sustainability_idx,
    }


def _blend_trained(
    form: Formulation, process: dict | None, props: dict[str, float]
) -> tuple[dict[str, float], dict[str, float]]:
    """Blend empirical predictions with trained models.

    Returns (blended_props, std_dict).  For metrics with a trained model:
    * sklearn RF: ensemble std from individual-tree predictions.
    * numpy ridge: RMSE from training set (a conservative constant estimate).
    Blending weight w = n / (n + K) converges to the model as data grows.
    """
    from .training import registry

    std: dict[str, float] = {}
    if not registry.info():
        return props, std
    vec = features.vector(form, process)
    K = 8.0
    for metric in list(props.keys()):
        out = registry.predict_with_std(form.domain, metric, vec)
        if out is None:
            continue
        model_pred, model_std, n = out
        w = n / (n + K)
        props[metric] = round(w * model_pred + (1.0 - w) * props[metric], 3)
        # Propagate uncertainty: blend model std with a nominal empirical std.
        empirical_std = abs(props[metric]) * 0.15  # 15% relative empirical uncertainty
        std[metric] = round(w * model_std + (1.0 - w) * empirical_std, 3)
    return props, std


def predict(form: Formulation, process: dict | None = None) -> dict[str, float]:
    """Return predicted properties keyed by metric name (point estimates only)."""
    props, _ = predict_full(form, process)
    return props


def predict_full(
    form: Formulation, process: dict | None = None
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (predicted, predicted_std) dicts including cost/VOC metrics."""
    props: dict[str, float] = {}
    mol = _molecular_features(form)

    if form.domain == ProductDomain.anticorrosion_coating:
        inhibitor = _sum_role(form, "inhibitor")
        binder = _sum_role(form, "resin") + _sum_role(form, "hardener")
        filler = _sum_role(form, "filler") + _sum_role(form, "pigment")
        ratio = amine_epoxy_ratio(form) or 2.5
        crosslink = max(0.0, 1.0 - abs(ratio - 2.0) / 3.0)
        salt_spray = (
            120.0 + inhibitor * 48.0 + binder * 6.5 + crosslink * 240.0
            - max(0.0, filler - 25.0) * 8.0
        )
        if _has_waterborne(form):
            salt_spray *= 0.9
        props["salt_spray_hours"] = round(max(0.0, salt_spray), 1)
        props["film_weight_gsm"] = round(binder * 1.6 + filler * 1.2, 1)
        props["adhesion_mpa"] = round(2.0 + crosslink * 6.0 + binder * 0.05, 2)
        props["pencil_hardness_idx"] = round(crosslink * 6.0 + binder * 0.04, 2)

    elif form.domain == ProductDomain.degreaser:
        surfactant = _sum_role(form, "surfactant")
        builder = _sum_role(form, "builder")
        solvent = sum(i.weight_pct for i in form.ingredients if i.role == "solvent" and i.name != "Deionized water")
        cleaning = 40.0 + surfactant * 3.2 + builder * 2.1 + solvent * 1.4
        props["cleaning_efficiency"] = round(min(99.0, cleaning), 1)
        props["foam_index"] = round(surfactant * 1.5, 2)
        props["bath_life_cycles"] = round(builder * 4.0 + 10.0, 0)

    else:  # surface_treatment
        active = _sum_role(form, "active")
        accel = _sum_role(form, "accelerator")
        inhibitor = _sum_role(form, "inhibitor")
        props["coating_weight_gsm"] = round(0.4 + active * 0.22 + accel * 2.5, 2)
        props["salt_spray_hours"] = round(48.0 + active * 9.0 + inhibitor * 30.0, 1)
        props["adhesion_promotion_idx"] = round(active * 0.6 + 1.0, 2)

    if mol and "salt_spray_hours" in props:
        props["salt_spray_hours"] = round(props["salt_spray_hours"] * (1.0 + 0.01 * mol["mean_logp"]), 1)

    # Cost / VOC / sustainability (always computed; not blended with trained models).
    props.update(_cost_and_sustainability(form))

    props, std = _blend_trained(form, process, props)
    return props, std


def objective_value(form: Formulation, objective: str, process: dict | None = None) -> float:
    """Scalar objective (higher is better) used by the single-objective optimizer."""
    props = predict(form, process)
    return float(props.get(objective, next(iter(props.values()), 0.0)))


def multi_objective_score(
    form: Formulation,
    objectives: list,  # list[ObjectiveSpec]
    process: dict | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> float:
    """Weighted aggregated score across multiple objectives.

    Each objective is min-max normalised using ``bounds`` (auto-provided by the
    optimizer) then multiplied by its weight.  ``direction='minimize'`` inverts
    the normalized value so higher is always better.
    """
    props = predict(form, process)
    bounds = bounds or {}
    total, total_weight = 0.0, 0.0
    for obj in objectives:
        val = props.get(obj.metric, 0.0)
        lo, hi = bounds.get(obj.metric, (0.0, 1.0))
        rng = hi - lo
        norm = (val - lo) / rng if rng > 1e-9 else 0.5
        if obj.direction == "minimize":
            norm = 1.0 - norm
        total += obj.weight * norm
        total_weight += obj.weight
    return total / total_weight if total_weight > 0 else 0.0
