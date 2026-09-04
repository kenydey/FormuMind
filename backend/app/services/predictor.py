"""Property prediction service (empirical surrogate + trained-model blend).

Predicts performance properties for metal surface treatment formulations.
Also computes economic & sustainability metrics (cost, VOC, sustainability
index) from the raw-material price/VOC metadata in the knowledge base.

The default implementation is a transparent empirical surrogate; trained
models are blended in automatically as lab data accumulates (see training.py).
Each metric is returned together with a ``predicted_std`` uncertainty estimate.
"""
from __future__ import annotations

import logging
from .errors import degrade_return
from ..domain import features, knowledge
from ..domain.chemistry import amine_epoxy_ratio, cpvc, normalize_role, pvc, solids_by_volume
from ..domain.schemas import Formulation, ProductDomain

logger = logging.getLogger(__name__)


def _sum_role(form: Formulation, role: str) -> float:
    return sum(i.weight_pct for i in form.ingredients if normalize_role(i.role) == role)


def _has_waterborne(form: Formulation) -> bool:
    from ..domain.chemistry import is_waterborne

    return is_waterborne(form)


_RDKIT_DESCRIPTORS = [
    ("MolWt",              "mean_molwt"),
    ("MolLogP",            "mean_logp"),
    ("TPSA",               "mean_tpsa"),
    ("NumHDonors",         "mean_hbd"),
    ("NumHAcceptors",      "mean_hba"),
    ("NumRotatableBonds",  "mean_rot_bonds"),
    ("FractionCSP3",       "mean_fsp3"),
    ("RingCount",          "mean_ring_count"),
]


def _molecular_features(form: Formulation) -> dict[str, float]:
    """RDKit-derived descriptors weighted by ingredient wt%; empty when RDKit absent.

    Computes 8 physicochemical descriptors for each SMILES-bearing component
    and returns the wt%-weighted mean. More informative than the single
    mean_logp previously returned.
    """
    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import Descriptors  # type: ignore
    except ImportError:
        return {}

    accum: dict[str, float] = {col: 0.0 for _, col in _RDKIT_DESCRIPTORS}
    total = 0.0
    for ing in form.ingredients:
        if not ing.smiles:
            continue
        mol = Chem.MolFromSmiles(ing.smiles)
        if mol is None:
            continue
        w = ing.weight_pct
        for rdkit_name, col in _RDKIT_DESCRIPTORS:
            val = getattr(Descriptors, rdkit_name, lambda m: 0.0)(mol)
            accum[col] += float(val) * w
        total += w

    if total <= 0:
        return {}
    return {col: round(v / total, 4) for col, v in accum.items()}


def _molformer_available() -> bool:
    """MoLFormer GPU path is permanently disabled (replaced by RDKit multi-descriptors)."""
    return False


def _molformer_features(form: Formulation) -> dict[str, float]:
    """GPU MoLFormer path — permanently inert; RDKit descriptors used instead."""
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
    except ImportError:
        return 1.3
    except Exception as exc:
        return degrade_return(logger, exc, "thermo density estimation", 1.3)
    return 1.3


def _pvc_metrics(form: Formulation) -> dict[str, float]:
    """Pigment volume concentration descriptors (empty for pigment-free systems).

    Reports PVC, solids-by-volume, and — when oil-absorption data exists for the
    pigment blend — CPVC and the PVC/CPVC ratio. The ratio is the key indicator
    of whether a film sits below CPVC (good barrier/gloss) or above it (porous).
    """
    pvc_val = pvc(form)
    if pvc_val <= 0:
        return {}
    out: dict[str, float] = {
        "pvc_pct": pvc_val,
        "solids_by_volume_pct": solids_by_volume(form),
    }
    cpvc_val = cpvc(form)
    if cpvc_val:
        out["cpvc_pct"] = cpvc_val
        out["pvc_to_cpvc_ratio"] = round(pvc_val / cpvc_val, 3)
    return out


def _color_metrics(form: Formulation) -> dict[str, float]:
    """CIELAB + ΔE₀₀ color descriptors; empty when no color-bearing pigment."""
    from .colorimetry import color_metrics

    return color_metrics(form)


def _rheology_metrics(form: Formulation) -> dict[str, float]:
    """Fox Tg and Mooney viscosity index; empty when data is insufficient."""
    try:
        from .rheology import fox_tg_celsius, mooney_viscosity, viscoelastic_index

        out: dict[str, float] = {}
        tg_c = fox_tg_celsius(form)
        if tg_c is not None:
            out["tg_celsius"] = tg_c
        eta_r = mooney_viscosity(form)
        if eta_r is not None:
            out["viscosity_relative"] = eta_r
        out["viscoelastic_index"] = viscoelastic_index(form)
        return out
    except ImportError:
        return {}


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
    cost_penalty = min(50.0, cost)  # 50 CNY/kg = full penalty
    sustainability_idx = round(max(0.0, 100.0 - voc_penalty - cost_penalty), 1)
    return {
        "cost_cny_per_kg": round(cost, 2),
        "voc_gpl": voc_gpl,
        "sustainability_idx": sustainability_idx,
    }


def _blend_trained(
    form: Formulation,
    process: dict | None,
    props: dict[str, float],
    std: dict[str, float] | None = None,
    *,
    project_id: str = "",
) -> tuple[dict[str, float], dict[str, float]]:
    """Blend empirical predictions with trained models.

    Returns (blended_props, std_dict).  For metrics with a trained model:
    * sklearn RF: ensemble std from individual-tree predictions.
    * numpy ridge: RMSE from training set (a conservative constant estimate).
    Blending weight w = n / (n + K) converges to the model as data grows.
    """
    from .training import registry

    out_std: dict[str, float] = dict(std or {})
    if not registry.info():
        return props, out_std
    vec = features.vector(form, process)
    pid = project_id or form.domain.value
    K = 8.0
    for metric in list(props.keys()):
        out = registry.predict_with_std(form.domain, metric, vec, project_id=pid)
        if out is None:
            out = registry.predict_with_std(form.domain, metric, vec, project_id="")
        if out is None:
            continue
        model_pred, model_std, n = out
        w = n / (n + K)
        empirical_pred = props[metric]
        props[metric] = round(w * model_pred + (1.0 - w) * props[metric], 3)
        # Propagate uncertainty: blend model std with a nominal empirical std.
        empirical_std = abs(empirical_pred) * 0.15  # 15% relative empirical uncertainty
        out_std[metric] = round(w * model_std + (1.0 - w) * empirical_std, 3)
    return props, out_std


def predict(form: Formulation, process: dict | None = None) -> dict[str, float]:
    """Return predicted properties keyed by metric name (point estimates only)."""
    props, _ = predict_full(form, process)
    return props


def _predict_mechanistic(
    form: Formulation, process: dict | None = None
) -> tuple[dict[str, float], dict[str, float]]:
    """Domain mechanistic + universal physics modules (no ML blend)."""
    props: dict[str, float] = {}
    mol = _molecular_features(form)

    if form.domain == ProductDomain.anticorrosion_coating:
        inhibitor = _sum_role(form, "inhibitor")
        binder = _sum_role(form, "resin") + _sum_role(form, "hardener")
        filler = _sum_role(form, "filler") + _sum_role(form, "pigment")
        ratio = amine_epoxy_ratio(form) or 2.5
        crosslink = max(0.0, 1.0 - abs(ratio - 3.0) / 3.0)
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
        solvent = _sum_role(form, "solvent")
        # 去离子水不算溶剂活性贡献，按名称排除。_sum_role 已走 normalize_role，
        # 不再裸比较 i.role（S6 根因：原始比对绕过角色归一化）。
        solvent_excl_water = sum(
            i.weight_pct for i in form.ingredients
            if normalize_role(i.role) == "solvent" and i.name != "Deionized water"
        )
        cleaning = 40.0 + surfactant * 3.2 + builder * 2.1 + solvent_excl_water * 1.4
        props["cleaning_efficiency"] = round(min(99.0, cleaning), 1)
        props["foam_index"] = round(surfactant * 1.5, 2)
        props["bath_life_cycles"] = round(builder * 4.0 + 10.0, 0)

    else:  # surface_treatment
        active = _sum_role(form, "active")
        accel = _sum_role(form, "accelerator")
        inhibitor = _sum_role(form, "inhibitor")
        resin = _sum_role(form, "resin") + _sum_role(form, "hardener")
        # 有机乳液型钝化（环氧丙烯酸/聚氨酯乳液等）以树脂成膜为主，耐盐雾与
        # 膜重远高于纯无机转化膜（锆/硅烷 48–200h → 720–1440h）。resin 系数为
        # 初始估计，量级对齐 LLM 领域知识预测，需实验数据校准。
        props["coating_weight_gsm"] = round(0.4 + active * 0.22 + accel * 2.5 + resin * 0.30, 2)
        props["salt_spray_hours"] = round(48.0 + active * 9.0 + inhibitor * 30.0 + resin * 38.0, 1)
        props["adhesion_promotion_idx"] = round(active * 0.6 + resin * 0.12 + 1.0, 2)

    if mol and "salt_spray_hours" in props:
        logp = mol.get("mean_logp", 0.0)
        tpsa = mol.get("mean_tpsa", 0.0)
        fsp3 = mol.get("mean_fsp3", 0.0)
        # Multi-descriptor correction: logP (barrier), TPSA (water permeation), fsp3 (flex)
        mol_correction = 1.0 + 0.01 * logp - 0.002 * (tpsa / 100.0) + 0.005 * fsp3
        props["salt_spray_hours"] = round(props["salt_spray_hours"] * mol_correction, 1)

    # Cost / VOC / sustainability (always computed; not blended with trained models).
    props.update(_cost_and_sustainability(form))
    # PVC / CPVC / solids-by-volume and CIELAB color (empty for pigment-free systems).
    props.update(_pvc_metrics(form))
    props.update(_color_metrics(form))
    # Rheology: Fox Tg, Mooney viscosity, viscoelastic index.
    props.update(_rheology_metrics(form))
    return props, {}


def predict_full(
    form: Formulation,
    process: dict | None = None,
    req=None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (predicted, predicted_std) dicts including cost/VOC metrics."""
    if req is not None:
        from .property.registry import predict_all

        props, std, tiers = predict_all(form, process, req)
        form.prediction_tiers = tiers
        return props, std

    props, _ = _predict_mechanistic(form, process)
    return _blend_trained(form, process, props)


def objective_value(form: Formulation, objective: str, process: dict | None = None) -> float:
    """Scalar objective (higher is better) used by the single-objective optimizer."""
    props = predict(form, process)
    return float(props.get(objective, 0.0))


def default_bounds(
    objectives: list,
    form: Formulation | None = None,
) -> dict[str, tuple[float, float]]:
    """Build normalization bounds from objective ref ranges or sensible defaults.

    Never derives bounds from a single formulation's predicted values, which
    would collapse every candidate's normalized score toward 0.5.
    """
    bounds: dict[str, tuple[float, float]] = {}
    for obj in objectives:
        if obj.ref_min is not None and obj.ref_max is not None:
            bounds[obj.metric] = (float(obj.ref_min), float(obj.ref_max))
            continue
        val = form.predicted.get(obj.metric, 0.0) if form is not None else 0.0
        if obj.metric in ("cost_cny_per_kg", "voc_gpl"):
            bounds[obj.metric] = (0.0, max(val * 2.0, 100.0))
        elif obj.metric == "salt_spray_hours":
            bounds[obj.metric] = (0.0, max(val * 2.0, 2000.0))
        else:
            bounds[obj.metric] = (0.0, max(val * 2.0, 1.0))
    return bounds


def multi_objective_score(
    form: Formulation,
    objectives: list,
    process: dict | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> float:
    """Weighted aggregated score across multiple objectives."""
    props = predict(form, process)
    bounds = bounds or {}
    total, total_weight = 0.0, 0.0
    for obj in objectives:
        val = props.get(obj.metric, 0.0)
        lo, hi = bounds.get(obj.metric, (0.0, 1.0))
        rng = hi - lo
        norm = (val - lo) / rng if rng > 1e-9 else 0.5
        norm = max(0.0, min(1.0, norm))
        if obj.direction == "minimize":
            norm = 1.0 - norm
        elif obj.direction == "match_target" and obj.target_value is not None:
            target_norm = (obj.target_value - lo) / rng if rng > 1e-9 else 0.5
            target_norm = max(0.0, min(1.0, target_norm))
            norm = 1.0 - abs(norm - target_norm)
        total += obj.weight * norm
        total_weight += obj.weight
    return total / total_weight if total_weight > 0 else 0.0


# ── Cold-start prewarm (R4, 2026-09-04) ─────────────────────────────────────
# 实测首个 predict 冷启动 9-29s 的构成: thermo 数据库核心初始化 ~7.5s(进程
# 一次) + 查不到的复杂成分名单次失败查询 ~10.6s(thermo 进程内缓存, 失败也
# 缓存, 故热态 predict 仅 0.08s); rdkit import ~1.5s。warm_predict() 把固定
# 成本(rdkit + thermo 数据库初始化 + 常见溶剂名)前置到 uvicorn lifespan /
# celery worker_process_init, 首个真实 predict 不再付数据库初始化。
# 幂等 + 失败静默: 预热永不阻断业务, 失败只意味着首个请求再付一次冷启动。
_warm_guard: dict[str, bool] = {"done": False}

_PREWARM_SOLVENTS = (
    "water", "ethanol", "isopropanol", "acetone",
    "xylene", "butanol", "propylene glycol",
)


def _do_warm() -> None:
    from rdkit import Chem  # noqa: F401
    from rdkit.Chem import Descriptors  # noqa: F401

    try:
        from thermo import Chemical

        for _name in _PREWARM_SOLVENTS:
            try:
                Chemical(_name)
            except Exception:
                pass
    except ImportError:
        pass  # thermo 缺席时密度链已回退 1.3 kg/L 名义值


def warm_predict() -> None:
    """Pre-load RDKit + thermo so the first real predict isn't cold.

    Idempotent (module-level guard) and failure-silent. Callable from the
    uvicorn lifespan (background thread) and the celery worker_process_init
    signal; safe to call from both in the same process.
    """
    if _warm_guard["done"]:
        return
    _warm_guard["done"] = True
    import time as _t

    _t0 = _t.time()
    try:
        _do_warm()
        logger.info("predictor prewarm done in %.1fs (rdkit+thermo hot)", _t.time() - _t0)
    except Exception:
        logger.exception("predictor warm_predict failed (non-fatal)")
