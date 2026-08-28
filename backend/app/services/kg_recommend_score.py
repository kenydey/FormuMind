"""KG → recommendation ranking adapter (second priority).

Turns KG material-compatibility signals into *soft* score adjustments on a
candidate ``Formulation``:

* An ``INHIBITS`` relation between two materials in the formulation skeleton
  multiplies ``form.score`` by ``settings.kg_inhibits_penalty`` (default 0.5)
  and appends a human-readable warning. The candidate sinks in the ranking but
  is never deleted — transparency over hard blocking.
* A ``SYNERGIZES`` relation (only when ``kg_synergizes_bonus > 1.0``) gives a
  mild multiplicative bonus. Disabled by default.

This complements the first-priority hard ``infeasible`` gate used inside the
DOE generation loop: the recommend path is soft (ranking), the loop path is
hard (candidate marking). Both consume the same deterministic KG source.

KG disabled (``kg_enabled is False``) → no-op, score untouched.
"""

from __future__ import annotations

import logging

from ..config import get_settings
from ..domain.schemas import Formulation
from .kg_chemical_check import ChemicalCheckResult, check_formulation_chemistry

logger = logging.getLogger(__name__)


def kg_compat_adjust(form: Formulation) -> ChemicalCheckResult:
    """Apply KG compatibility adjustments to ``form.score`` in place.

    Returns the underlying ``ChemicalCheckResult`` so callers can record it on
    the formulation for transparency. No-op (feasible pass) when KG is off.
    """
    settings = get_settings()
    if not settings.kg_enabled:
        return ChemicalCheckResult(feasible=True, status="pass")

    chk = check_formulation_chemistry(form, include_synergies=True)

    penalty = float(settings.kg_inhibits_penalty)
    bonus = float(settings.kg_synergizes_bonus)
    measured_bonus = float(getattr(settings, "kg_measured_bonus", 1.15))

    if not chk.feasible:
        # INHIBITS hit → sink the candidate.
        if form.score is not None and penalty < 1.0:
            form.score = float(form.score) * penalty
        form.warnings.append(
            "知识图谱化学相容性告警：" + "；".join(chk.reasons)
        )
    elif bonus > 1.0 and chk.synergy_pairs:
        # Optional SYNERGIZES boost (off by default).
        if form.score is not None:
            form.score = float(form.score) * bonus

    # 实测验证加成：在 feasible 且材料有 measured 证据时提升排名
    if chk.feasible and chk.measured_materials and measured_bonus > 1.0:
        if form.score is not None:
            form.score = float(form.score) * measured_bonus
        # 透明：追加提示（不与不相容告警混淆）
        form.warnings.append(
            "实测验证加成：配方含实测验证材料 " + "、".join(chk.measured_materials)
        )

    record_kg_compat(form, chk)
    return chk


def record_kg_compat(form: Formulation, chk: ChemicalCheckResult) -> None:
    """Stash KG adjustment detail on the formulation for UI transparency."""
    form.kg_compat = {
        "feasible": chk.feasible,
        "status": chk.status,
        "incompatible_pairs": [
            {"a": a, "b": b, "relation": rel} for a, b, rel in chk.incompatible_pairs
        ],
        "synergy_pairs": [
            {"a": a, "b": b, "relation": rel} for a, b, rel in chk.synergy_pairs
        ],
        "measured_materials": list(chk.measured_materials),
        "reasons": chk.reasons,
    }
