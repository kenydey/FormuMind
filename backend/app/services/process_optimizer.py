"""Manufacturing process parameter optimizer (v0.5).

Optimizes orthogonal process parameters (cure temperature, film thickness,
dispersion speed, etc.) independently of formulation composition. Uses the
same BayesianOptimizer / Optuna / BoTorch chain as the composition optimizer
but operates in a process-parameter space.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.schemas import (
    ObjectiveSpec,
    ProcessOptRequest,
    ProcessOptResult,
    ProductDomain,
)

# ── Process factor definitions per domain ────────────────────────────────────

@dataclass
class ProcessFactor:
    name: str
    unit: str
    low: float
    high: float
    description: str = ""


PROCESS_LEVERS: dict[ProductDomain, list[ProcessFactor]] = {
    ProductDomain.anticorrosion_coating: [
        ProcessFactor("cure_temperature_c", "°C", 60.0, 120.0, "Cure / baking temperature"),
        ProcessFactor("cure_time_min",      "min", 20.0,  90.0, "Cure / baking duration"),
        ProcessFactor("dispersion_rpm",     "rpm", 800.0, 2400.0, "High-speed disperser speed"),
        ProcessFactor("film_thickness_um",  "μm",  40.0, 120.0, "Dry film thickness"),
    ],
    ProductDomain.degreaser: [
        ProcessFactor("bath_temperature_c",  "°C",  40.0,  80.0, "Process bath temperature"),
        ProcessFactor("immersion_time_min",  "min",  2.0,  15.0, "Immersion time"),
        ProcessFactor("ph_setpoint",         "pH",   8.0,  13.0, "Bath pH"),
    ],
    ProductDomain.surface_treatment: [
        ProcessFactor("treat_temperature_c", "°C",  20.0,  55.0, "Treatment bath temperature"),
        ProcessFactor("immersion_time_min",  "min",  5.0,  20.0, "Treatment immersion time"),
        ProcessFactor("accelerator_factor",  "×",   0.5,   2.0, "Accelerator concentration multiplier"),
    ],
}


# ── Property prediction from process parameters ──────────────────────────────

def _predict_anticorrosion(params: dict[str, float]) -> dict[str, float]:
    """Arrhenius cure conversion + barrier/thickness corrections for coatings."""
    T_c = params.get("cure_temperature_c", 80.0)
    t_min = params.get("cure_time_min", 45.0)
    rpm = params.get("dispersion_rpm", 1600.0)
    thickness_um = params.get("film_thickness_um", 80.0)

    # Arrhenius cure conversion (Ea ≈ 45 kJ/mol, k0 = 1e10 min^-1)
    Ea_R = 45000.0 / 8.314  # K
    T_k = T_c + 273.15
    k = 1e10 * math.exp(-Ea_R / T_k)
    conversion = 1.0 - math.exp(-k * t_min)  # fraction

    # Dispersion uniformity index (penalise too low or too high rpm)
    rpm_opt = 1600.0
    dispersion_idx = math.exp(-((rpm - rpm_opt) / 600.0) ** 2)

    # Salt-spray improvement: linear with thickness (plateau at 120 μm)
    thickness_factor = min(1.5, 0.5 + thickness_um / 120.0)

    salt_spray = 250.0 * conversion * dispersion_idx * thickness_factor
    film_uniformity = round(100.0 * dispersion_idx, 1)

    return {
        "cure_conversion_pct": round(conversion * 100, 1),
        "salt_spray_improvement_h": round(max(0.0, salt_spray), 1),
        "film_uniformity_pct": film_uniformity,
        "film_thickness_um": round(thickness_um, 1),
    }


def _predict_degreaser(params: dict[str, float]) -> dict[str, float]:
    """Q10 temperature model for cleaning efficiency + foam/pH checks."""
    T_c = params.get("bath_temperature_c", 60.0)
    t_min = params.get("immersion_time_min", 8.0)
    ph = params.get("ph_setpoint", 12.0)

    # Q10 factor ≈ 1.4 per 10 °C above 40 °C reference
    T_ref = 40.0
    q10_factor = 1.4 ** ((T_c - T_ref) / 10.0)
    # Time contribution (diminishing returns after ~10 min)
    time_factor = 1.0 - math.exp(-t_min / 5.0)
    # pH efficiency (peak around 12–13 for alkaline, lower near-neutral)
    ph_factor = min(1.0, (ph - 7.0) / 5.0) if ph >= 7 else 0.3

    cleaning = 60.0 * q10_factor * time_factor * ph_factor
    # Foam penalty at high temperature + high pH
    foam_penalty = max(0.0, (T_c - 70.0) / 10.0 * (ph - 11.0) / 2.0)

    return {
        "cleaning_efficiency_pct": round(min(99.0, cleaning), 1),
        "foam_index": round(max(0.0, 1.0 - foam_penalty * 0.2), 2),
        "bath_temperature_c": round(T_c, 1),
    }


def _predict_surface_treatment(params: dict[str, float]) -> dict[str, float]:
    """Power-law phosphate coating weight model."""
    T_c = params.get("treat_temperature_c", 40.0)
    t_min = params.get("immersion_time_min", 10.0)
    acc = params.get("accelerator_factor", 1.0)

    # Empirical power law: W = A · T^0.5 · t^0.4 · accelerator
    A = 0.05
    coating_weight = A * (T_c ** 0.5) * (t_min ** 0.4) * acc
    adhesion_idx = round(1.0 + coating_weight * 0.3, 2)

    return {
        "coating_weight_gsm": round(max(0.2, coating_weight), 2),
        "adhesion_promotion_idx": round(min(5.0, adhesion_idx), 2),
        "treat_temperature_c": round(T_c, 1),
    }


_PREDICT_FN = {
    ProductDomain.anticorrosion_coating: _predict_anticorrosion,
    ProductDomain.degreaser: _predict_degreaser,
    ProductDomain.surface_treatment: _predict_surface_treatment,
}


def predict_process_outcome(domain: ProductDomain, params: dict[str, float]) -> dict[str, float]:
    """Predict process-level KPIs from process parameters."""
    fn = _PREDICT_FN.get(domain)
    if fn is None:
        return {}
    return fn(params)


# ── Default objectives per domain ────────────────────────────────────────────

_DEFAULT_PROCESS_OBJECTIVES: dict[ProductDomain, list[ObjectiveSpec]] = {
    ProductDomain.anticorrosion_coating: [
        ObjectiveSpec(metric="salt_spray_improvement_h", weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="cure_conversion_pct",     weight=0.3, direction="maximize"),
        ObjectiveSpec(metric="film_uniformity_pct",     weight=0.2, direction="maximize"),
    ],
    ProductDomain.degreaser: [
        ObjectiveSpec(metric="cleaning_efficiency_pct", weight=0.6, direction="maximize"),
        ObjectiveSpec(metric="foam_index",              weight=0.4, direction="maximize"),
    ],
    ProductDomain.surface_treatment: [
        ObjectiveSpec(metric="coating_weight_gsm",      weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="adhesion_promotion_idx",  weight=0.5, direction="maximize"),
    ],
}


def _composite_score(
    outcome: dict[str, float],
    objectives: list[ObjectiveSpec],
    bounds: dict[str, tuple[float, float]],
) -> float:
    """Weighted normalized score, consistent with predictor.multi_objective_score."""
    total = weight_sum = 0.0
    for obj in objectives:
        val = outcome.get(obj.metric, 0.0)
        lo, hi = bounds.get(obj.metric, (0.0, 1.0))
        rng = hi - lo
        norm = (val - lo) / rng if rng > 1e-9 else 0.5
        if obj.direction == "minimize":
            norm = 1.0 - norm
        total += obj.weight * norm
        weight_sum += obj.weight
    return total / weight_sum if weight_sum > 0 else 0.0


def run_process_optimization(req: ProcessOptRequest) -> ProcessOptResult:
    """Optimize process parameters using the best available optimizer engine."""
    from .optimizer import Factor, build_optimizer

    domain = req.domain
    levers = PROCESS_LEVERS.get(domain, [])
    objectives = req.objectives or _DEFAULT_PROCESS_OBJECTIVES.get(domain, [])
    factors = [Factor(name=pf.name, low=pf.low, high=pf.high) for pf in levers]
    opt = build_optimizer(factors=factors, seed=7)

    # Seed bounds from the midpoint of each factor
    mid_params = {pf.name: (pf.low + pf.high) / 2 for pf in levers}
    mid_outcome = predict_process_outcome(domain, mid_params)
    bounds: dict[str, tuple[float, float]] = {}
    for metric, val in mid_outcome.items():
        bounds[metric] = (val * 0.3, val * 1.8) if val > 0 else (0.0, 1.0)

    history: list[float] = []
    best_score = float("-inf")
    best_params: dict[str, float] = mid_params

    for _ in range(req.iterations):
        x = opt.suggest()
        params = {f.name: v for f, v in zip(factors, x)}
        outcome = predict_process_outcome(domain, params)
        # Update running bounds
        for metric, val in outcome.items():
            lo, hi = bounds.get(metric, (val, val))
            bounds[metric] = (min(lo, val), max(hi, val))
        score = _composite_score(outcome, objectives, bounds)
        opt.observe(x, score)
        if score > best_score:
            best_score = score
            best_params = params
        history.append(round(max(best_score, 0.0), 3))

    best_outcome = predict_process_outcome(domain, best_params)
    return ProcessOptResult(
        domain=domain.value,
        iterations=req.iterations,
        engine=getattr(opt, "engine", "numpy-ucb"),
        history=history,
        best_params={k: round(v, 3) for k, v in best_params.items()},
        predicted_outcome={k: round(v, 3) for k, v in best_outcome.items()},
    )
