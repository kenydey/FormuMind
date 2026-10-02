"""Rebuild a Formulation from DOE/experiment factor values.

Kept dependency-light (only the domain knowledge base) so it can be imported by
both the optimization workflow and the training service without import cycles.
"""
from __future__ import annotations

import logging

from ..domain import knowledge
from ..domain.levers import is_process_lever
from ..domain.project_spec import normalize_requirement, resolve_levers
from ..domain.schemas import Formulation, ProductDomain, Requirement

logger = logging.getLogger(__name__)

# Dilute aqueous baths: g/L ≈ wt% × 10 (density ~1 g/mL).
_G_PER_L_TO_WT_PCT = 0.1


def formulation_from_factors(
    req: Requirement | ProductDomain,
    factors: dict[str, float],
) -> Formulation:
    """Apply natural-unit lever values onto the substrate-aware baseline formulation.

    Keys matching an ingredient name override that ingredient's weight percent;
    process keys (e.g. ``cure_temperature_c``, ``immersion_time_min``) are ignored
    here and handled as process features elsewhere. Weights are re-balanced to ~100%.
    """
    requirement = req if isinstance(req, Requirement) else Requirement(domain=req)
    requirement = normalize_requirement(requirement)
    # Mirror run_optimization's own lever resolution (`req.active_formulation or
    # base`): reconstructing from the generic template regardless meant every
    # override was matched against ingredient names the active formulation
    # doesn't have, so overrides silently applied to nothing and every
    # candidate this returned was the same static baseline — the optimizer
    # search degenerated to repeatedly scoring one unchanging point.
    base = requirement.active_formulation or knowledge.baseline_formulation(requirement)
    levers = resolve_levers(requirement, base)
    unit_map = {lev.name: lev.unit for lev in levers}
    # U-5: 离散材料替换语义 —— lever 上的水平→成分名映射。
    # v8: getattr 防御（某些调用路径可能传 dict）。
    material_maps = {
        lev.name: getattr(lev, "material_map", None)
        for lev in levers
        if getattr(lev, "material_map", None)
    }
    overrides = dict(factors)
    existing_names = {ing.name for ing in base.ingredients}
    ings = []
    for ing in base.ingredients:
        new = ing.model_copy(deep=True)
        if new.name in overrides and not is_process_lever(new.name):
            raw_value = overrides[new.name]
            # U-5: 字符串水平 + material_map → 成分身份替换（wt% 不变，
            # 配比骨架由数值因子负责）。无映射则保持 B-DOE-3 跳过。
            mat_map = material_maps.get(new.name)
            if isinstance(raw_value, str) and mat_map and raw_value in mat_map:
                target = mat_map[raw_value]
                if target != new.name and target in existing_names:
                    logger.warning(
                        "U-5 material replacement skipped: %r already exists in formulation",
                        target,
                    )
                else:
                    logger.info(
                        "U-5 material replacement: %r → %r (level %r)",
                        new.name, target, raw_value,
                    )
                    existing_names.discard(new.name)
                    new.name = target
                    existing_names.add(target)
                ings.append(new)
                continue
            # B-DOE-3: 离散因子的 natural 值可能是字符串水平（如材料种类），
            # 不是重量——跳过 weight 覆盖，不做 float() 猜测。
            try:
                raw = float(raw_value)
            except (TypeError, ValueError):
                ings.append(new)
                continue
            unit = unit_map.get(new.name, "wt%")
            if unit == "g/L":
                raw *= _G_PER_L_TO_WT_PCT
            new.weight_pct = round(raw, 4)
        ings.append(new)
    return knowledge._balanced(base.name, base.domain, ings, base.rationale)


def genome_from_requirement(req: Requirement | ProductDomain):
    """Baseline formulation as a genome — the starting point for genome search.

    Carries the lever units across so a g/L surface-treatment slot survives the
    round trip; the factor path recovers those by ingredient-name lookup, which
    stops working the moment a slot's material can change.
    """
    from ..domain.genome import genome_from_formulation

    requirement = req if isinstance(req, Requirement) else Requirement(domain=req)
    requirement = normalize_requirement(requirement)
    base = knowledge.baseline_formulation(requirement)
    units = {lev.name: lev.unit for lev in resolve_levers(requirement, base)}
    return genome_from_formulation(base, units=units)


def formulation_from_genome(req: Requirement | ProductDomain, genome, *, strict: bool = True):
    """Genome → Formulation. Sibling of ``formulation_from_factors``.

    The factor path can only rescale ingredients already present in the
    hardcoded baseline template; this one lets the composition itself vary,
    which is what inverse design and material substitution both need.

    ``strict=False`` keeps unknown materials (LLM/recommend slots not yet in
    the catalog) so substitution can still compute deltas; inverse design
    keeps the default strict path.
    """
    from ..domain.genome import formulation_from_genome as _from_genome

    return _from_genome(req, genome, strict=strict)
