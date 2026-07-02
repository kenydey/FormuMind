"""Physics simulation service (reserved + analytic fallback).

The full pipeline targets reactive MD cure simulations via HTPolyNet / LUNAR /
LAMMPS (mounted as Docker images). Those are heavy and asynchronous, so this
module provides an analytic approximation that returns the same
``SimulationReport`` structure: cross-link conversion, gel point and a glass
transition estimate derived from the formulation. This lets the DOE-verification
and 3D-handoff contracts be exercised today.
"""
from __future__ import annotations

import logging
from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
from dataclasses import dataclass, field

from ..domain.chemistry import amine_epoxy_ratio
from ..domain.schemas import Formulation, ProductDomain

logger = logging.getLogger(__name__)


@dataclass
class SimulationReport:
    engine: str
    converged: bool
    metrics: dict[str, float] = field(default_factory=dict)
    trajectory_ref: str | None = None  # placeholder for 3Dmol/OVITO handoff
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "converged": self.converged,
            "metrics": self.metrics,
            "trajectory_ref": self.trajectory_ref,
            "notes": self.notes,
        }


def _heavy_available() -> bool:
    # Reserved future hook for reactive-MD cure simulation: HTPolyNet builds the
    # thermoset crosslink network and LUNAR handles LAMMPS pre/post-processing
    # (force-field assignment, fix bond/react topology, Tg / free-volume). Both
    # run via the Docker ``heavy`` profile, so this module only probes for ASE as
    # a lightweight proxy and otherwise returns the analytic approximation.
    try:  # pragma: no cover - heavy deps
        import ase  # noqa: F401

        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def simulate_cure(form: Formulation, cure_temp_c: float = 80.0) -> SimulationReport:
    """Approximate a thermoset cure / film-formation process."""
    engine = "LAMMPS/HTPolyNet (reserved)" if _heavy_available() else "analytic-approximation"

    if form.domain == ProductDomain.anticorrosion_coating:
        ratio = amine_epoxy_ratio(form) or 2.5
        # Conversion is highest near stoichiometric balance and adequate temperature.
        stoich = max(0.0, 1.0 - abs(ratio - 2.0) / 3.0)
        thermal = min(1.0, cure_temp_c / 120.0)
        conversion = round(min(0.98, 0.55 + 0.4 * stoich * thermal), 3)
        tg = round(40.0 + conversion * 80.0, 1)
        return SimulationReport(
            engine=engine,
            converged=conversion > 0.7,
            metrics={"crosslink_conversion": conversion, "tg_c": tg, "gel_fraction": round(conversion * 0.95, 3)},
            notes="Cross-link conversion vs. resin:hardener stoichiometry and cure temperature.",
        )

    if form.domain == ProductDomain.surface_treatment:
        active = sum(i.weight_pct for i in form.ingredients if i.role == "active")
        coverage = round(min(1.0, 0.3 + active * 0.06), 3)
        return SimulationReport(
            engine=engine,
            converged=coverage > 0.5,
            metrics={"film_coverage": coverage, "interface_energy_idx": round(active * 0.5, 2)},
            notes="Conversion-film coverage from active concentration (interface model).",
        )

    # degreaser: surfactant micelle / wetting proxy
    surf = sum(i.weight_pct for i in form.ingredients if i.role == "surfactant")
    wetting = round(min(1.0, 0.4 + surf * 0.07), 3)
    return SimulationReport(
        engine=engine,
        converged=True,
        metrics={"wetting_index": wetting, "micelle_density_idx": round(surf * 0.5, 2)},
        notes="Wetting / micellisation proxy from surfactant loading.",
    )
