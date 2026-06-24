"""Example project templates (legacy three domains as loadable demos)."""
from __future__ import annotations

from ..schemas import LeverSpec, MaterialSpec, MetricPriorSpec, ObjectiveSpec, ProductDomain, Requirement, Substrate

EXAMPLE_PROJECTS: dict[str, dict] = {
    "anticorrosion_coating": {
        "id": "anticorrosion_coating",
        "label": "防腐蚀涂料 · Anti-corrosion",
        "product_type": "防腐蚀环氧底漆",
        "application": "carbon_steel",
        "domain": ProductDomain.anticorrosion_coating,
        "objectives": [
            ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize", target_value=500),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.25, direction="minimize"),
            ObjectiveSpec(metric="sustainability_idx", weight=0.25, direction="maximize"),
        ],
        "levers": [
            LeverSpec(name="Zinc phosphate", low=2.0, high=14.0),
            LeverSpec(name="Bisphenol-A epoxy (DGEBA)", low=28.0, high=48.0),
            LeverSpec(name="Polyamide hardener", low=8.0, high=22.0),
            LeverSpec(name="cure_temperature_c", low=50.0, high=80.0, unit="C"),
        ],
    },
    "degreaser": {
        "id": "degreaser",
        "label": "脱脂剂 · Degreaser",
        "product_type": "工业脱脂剂",
        "application": "carbon_steel",
        "domain": ProductDomain.degreaser,
        "objectives": [
            ObjectiveSpec(metric="cleaning_efficiency", weight=0.5, direction="maximize", target_value=90),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.3, direction="minimize"),
            ObjectiveSpec(metric="voc_gpl", weight=0.2, direction="minimize"),
        ],
        "levers": [
            LeverSpec(name="Nonionic surfactant (C12-14 EO7)", low=2.0, high=12.0),
            LeverSpec(name="Sodium metasilicate", low=2.0, high=14.0),
        ],
    },
    "surface_treatment": {
        "id": "surface_treatment",
        "label": "表面处理剂 · Surface treatment",
        "product_type": "磷化转化膜",
        "application": "carbon_steel",
        "domain": ProductDomain.surface_treatment,
        "objectives": [
            ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize"),
            ObjectiveSpec(metric="coating_weight_gsm", weight=0.2, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.3, direction="minimize"),
        ],
        "levers": [
            LeverSpec(name="Phosphoric acid", low=3.0, high=14.0),
            LeverSpec(name="Manganese dihydrogen phosphate", low=1.0, high=8.0),
        ],
    },
}

BUILTIN_METRICS = [
    "salt_spray_hours",
    "cleaning_efficiency",
    "cost_cny_per_kg",
    "voc_gpl",
    "sustainability_idx",
    "film_weight_gsm",
    "coating_weight_gsm",
    "adhesion_mpa",
    "pencil_hardness_idx",
    "tg_celsius",
    "viscosity_relative",
]

ROLE_CATALOG = [
    "resin", "hardener", "inhibitor", "pigment", "filler", "surfactant",
    "builder", "solvent", "active", "accelerator", "chelant", "additive",
]


def load_example(example_id: str) -> Requirement:
    """Build a Requirement from a built-in example project."""
    ex = EXAMPLE_PROJECTS.get(example_id)
    if ex is None:
        raise KeyError(f"Unknown example project {example_id!r}")
    return Requirement(
        project_id=ex["id"],
        product_type=ex["product_type"],
        application=ex["application"],
        domain=ex["domain"],
        substrate=Substrate.carbon_steel,
        salt_spray_hours=500 if ex["domain"] == ProductDomain.anticorrosion_coating else 0,
        film_weight_gsm=70 if ex["domain"] == ProductDomain.anticorrosion_coating else 0,
        cure_temperature_c=80 if ex["domain"] == ProductDomain.anticorrosion_coating else None,
        cleaning_efficiency=90 if ex["domain"] == ProductDomain.degreaser else 0,
        voc_limit_gpl=420,
        objectives=list(ex["objectives"]),
        levers=list(ex["levers"]),
    )
