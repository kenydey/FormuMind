"""Domain knowledge base for metal surface treatment formulation.

This module encodes curated, real-world-plausible ingredient libraries and
baseline formulation templates for the three product families. It is the
substantive core that powers the deterministic offline recommendations,
stoichiometry checks, and the empirical property predictor — so the platform
produces sensible chemistry even with no LLM or heavy library installed.

Values are engineering-reasonable starting points for R&D screening, not
production specifications.
"""
from __future__ import annotations

from .schemas import Formulation, Ingredient, ProductDomain, Requirement, Substrate

# --- Raw material library ------------------------------------------------
# Fields: role, formula, smiles, molar_mass (g/mol),
#         price_cny_per_kg (engineering estimate),
#         voc_contrib (mass fraction that becomes VOC, 0-1)
# Optional fields used by the PVC/CPVC and colorimetry engines (all optional;
# absent → role-based nominal density and CPVC/ΔE degrade gracefully):
#         density_gcm3 (g/cm³), oil_absorption (g oil / 100 g pigment),
#         lab (CIELAB [L*, a*, b*] for particulate pigments)
# Optional fields used by rheology / safety engines:
#         tg_k (glass-transition temp K, for Fox-equation Tg prediction)
#         svhc (bool, EU REACH SVHC candidate)
RAW_MATERIALS: dict[str, dict] = {
    # Resins / film formers (anti-corrosion)
    "Bisphenol-A epoxy (DGEBA)": {
        "role": "resin", "formula": "C21H24O4",
        "smiles": "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1", "molar_mass": 340.41,
        "price_cny_per_kg": 28.0, "voc_contrib": 0.0,
        "tg_k": 253.0,  # uncured DGEBA Tg ≈ −20 °C
    },
    "Waterborne acrylic emulsion": {
        "role": "resin", "formula": None, "smiles": "CCOC(=O)C(C)=C", "molar_mass": 100.12,
        "price_cny_per_kg": 18.0, "voc_contrib": 0.02,
        "tg_k": 278.0,  # acrylic latex Tg ≈ +5 °C
    },
    "Polyurethane dispersion": {
        "role": "resin", "formula": None, "smiles": None, "molar_mass": None,
        "price_cny_per_kg": 32.0, "voc_contrib": 0.02,
        "tg_k": 233.0,  # PUD Tg ≈ −40 °C
    },
    "Zinc-rich epoxy binder": {
        "role": "resin", "formula": None, "smiles": None, "molar_mass": None,
        "price_cny_per_kg": 35.0, "voc_contrib": 0.0,
        "tg_k": 263.0,  # zinc-rich epoxy Tg ≈ −10 °C
    },
    # Hardeners / curing agents
    "Polyamide hardener": {
        "role": "hardener", "formula": None, "smiles": None, "molar_mass": None,
        "price_cny_per_kg": 22.0, "voc_contrib": 0.0,
        "tg_k": 313.0,  # polyamide hardener Tg ≈ +40 °C
    },
    "Isophorone diamine (IPDA)": {
        "role": "hardener", "formula": "C10H22N2",
        "smiles": "CC1(C)CC(N)CC(C)(CN)C1", "molar_mass": 170.30,
        "price_cny_per_kg": 55.0, "voc_contrib": 0.08,
    },
    "Blocked isocyanate (IPDI)": {
        "role": "hardener", "formula": "C12H18N2O2",
        "smiles": "O=C=NC1CC(C)(C)CC(CN=C=O)C1", "molar_mass": 222.28,
        "price_cny_per_kg": 65.0, "voc_contrib": 0.0,
    },
    # Corrosion inhibitors / passivators
    "Zinc phosphate": {
        "role": "inhibitor", "formula": "Zn3(PO4)2", "smiles": None, "molar_mass": 386.11,
        "price_cny_per_kg": 12.0, "voc_contrib": 0.0,
        "density_gcm3": 3.1, "oil_absorption": 25.0, "lab": [92.0, -0.5, 2.0],
    },
    "Zinc molybdate": {
        "role": "inhibitor", "formula": "ZnMoO4", "smiles": None, "molar_mass": 225.33,
        "price_cny_per_kg": 45.0, "voc_contrib": 0.0,
        "density_gcm3": 4.3, "oil_absorption": 20.0, "lab": [95.0, -1.0, 3.0],
        "svhc": True,
    },
    "Cerium nitrate": {
        "role": "inhibitor", "formula": "Ce(NO3)3", "smiles": None, "molar_mass": 326.13,
        "price_cny_per_kg": 120.0, "voc_contrib": 0.0,
        "svhc": True,
    },
    "2-Mercaptobenzothiazole": {
        "role": "inhibitor", "formula": "C7H5NS2",
        "smiles": "c1ccc2c(c1)nc(s2)S", "molar_mass": 167.25,
        "price_cny_per_kg": 28.0, "voc_contrib": 0.0,
    },
    # Pigments / fillers
    "Titanium dioxide": {
        "role": "pigment", "formula": "TiO2", "smiles": None, "molar_mass": 79.87,
        "price_cny_per_kg": 18.0, "voc_contrib": 0.0,
        "density_gcm3": 4.0, "oil_absorption": 18.0, "lab": [97.0, -0.6, 1.8],
    },
    "Talc": {
        "role": "filler", "formula": "Mg3Si4O10(OH)2", "smiles": None, "molar_mass": 379.27,
        "price_cny_per_kg": 3.0, "voc_contrib": 0.0,
        "density_gcm3": 2.75, "oil_absorption": 35.0, "lab": [90.0, -0.8, 2.5],
    },
    "Fumed silica": {
        "role": "filler", "formula": "SiO2", "smiles": None, "molar_mass": 60.08,
        "price_cny_per_kg": 30.0, "voc_contrib": 0.0,
        "density_gcm3": 2.2, "oil_absorption": 100.0, "lab": [94.0, -0.3, 1.0],
    },
    # Solvents
    "Xylene": {
        "role": "solvent", "formula": "C8H10", "smiles": "Cc1ccccc1C", "molar_mass": 106.17,
        "price_cny_per_kg": 8.0, "voc_contrib": 1.0,
    },
    "Deionized water": {
        "role": "solvent", "formula": "H2O", "smiles": "O", "molar_mass": 18.02,
        "price_cny_per_kg": 0.01, "voc_contrib": 0.0,
    },
    "Butyl glycol": {
        "role": "solvent", "formula": "C6H14O2", "smiles": "CCCCOCCO", "molar_mass": 118.17,
        "price_cny_per_kg": 14.0, "voc_contrib": 0.95,
    },
    # Degreaser actives
    "Sodium hydroxide": {
        "role": "builder", "formula": "NaOH", "smiles": None, "molar_mass": 40.00,
        "price_cny_per_kg": 3.5, "voc_contrib": 0.0,
    },
    "Sodium metasilicate": {
        "role": "builder", "formula": "Na2SiO3", "smiles": None, "molar_mass": 122.06,
        "price_cny_per_kg": 5.0, "voc_contrib": 0.0,
    },
    "Sodium tripolyphosphate": {
        "role": "builder", "formula": "Na5P3O10", "smiles": None, "molar_mass": 367.86,
        "price_cny_per_kg": 7.0, "voc_contrib": 0.0,
    },
    "Nonionic surfactant (C12-14 EO7)": {
        "role": "surfactant", "formula": None, "smiles": None, "molar_mass": 600.0,
        "price_cny_per_kg": 22.0, "voc_contrib": 0.0,
    },
    "Sodium gluconate": {
        "role": "chelant", "formula": "C6H11NaO7", "smiles": None, "molar_mass": 218.14,
        "price_cny_per_kg": 12.0, "voc_contrib": 0.0,
    },
    "D-Limonene": {
        "role": "solvent", "formula": "C10H16",
        "smiles": "CC(=C)C1CCC(C)=CC1", "molar_mass": 136.24,
        "price_cny_per_kg": 25.0, "voc_contrib": 0.85,
    },
    # Surface treatment actives
    "Phosphoric acid": {
        "role": "active", "formula": "H3PO4", "smiles": "OP(=O)(O)O", "molar_mass": 97.99,
        "price_cny_per_kg": 5.0, "voc_contrib": 0.0,
    },
    "Zinc oxide": {
        "role": "active", "formula": "ZnO", "smiles": None, "molar_mass": 81.38,
        "price_cny_per_kg": 20.0, "voc_contrib": 0.0,
    },
    "Manganese dihydrogen phosphate": {
        "role": "active", "formula": "Mn(H2PO4)2", "smiles": None, "molar_mass": 248.94,
        "price_cny_per_kg": 18.0, "voc_contrib": 0.0,
    },
    "Sodium nitrite": {
        "role": "accelerator", "formula": "NaNO2", "smiles": None, "molar_mass": 69.00,
        "price_cny_per_kg": 6.0, "voc_contrib": 0.0,
        "svhc": True,
    },
    "Hexafluorozirconic acid": {
        "role": "active", "formula": "H2ZrF6", "smiles": None, "molar_mass": 208.23,
        "price_cny_per_kg": 95.0, "voc_contrib": 0.0,
    },
    "(3-Aminopropyl)triethoxysilane (APTES)": {
        "role": "active", "formula": "C9H23NO3Si",
        "smiles": "CCO[Si](OCC)(OCC)CCCN", "molar_mass": 221.37,
        "price_cny_per_kg": 85.0, "voc_contrib": 0.55,
    },
}


def ingredient(name: str, weight_pct: float) -> Ingredient:
    spec = RAW_MATERIALS.get(name, {})
    return Ingredient(
        name=name,
        role=spec.get("role", "additive"),
        smiles=spec.get("smiles"),
        formula=spec.get("formula"),
        molar_mass=spec.get("molar_mass"),
        weight_pct=weight_pct,
    )


# --- Baseline templates per domain --------------------------------------
def _anticorrosion_template(req: Requirement) -> Formulation:
    waterborne = req.voc_limit_gpl < 250 or req.cure_temperature_c < 60
    if waterborne:
        resin, solvent = "Waterborne acrylic emulsion", "Deionized water"
    else:
        resin, solvent = "Bisphenol-A epoxy (DGEBA)", "Xylene"
    # Higher salt-spray targets push more inhibitor and binder.
    inhibitor_pct = min(12.0, 4.0 + req.salt_spray_hours / 200.0)
    ings = [
        ingredient(resin, 38.0),
        ingredient("Polyamide hardener", 14.0),
        ingredient("Zinc phosphate", round(inhibitor_pct, 2)),
        ingredient("Titanium dioxide", 10.0),
        ingredient("Talc", 12.0),
        ingredient("Fumed silica", 2.0),
        ingredient(solvent, round(24.0 - inhibitor_pct + 4.0, 2)),
    ]
    return _balanced("Anti-corrosion primer (baseline)", ProductDomain.anticorrosion_coating, ings,
                     "Two-component primer: epoxy/acrylic binder cross-linked with polyamide, "
                     "active zinc-phosphate passivation scaled to the salt-spray target.")


def _degreaser_template(req: Requirement) -> Formulation:
    ph = req.ph_target if req.ph_target is not None else 12.5
    alkaline = ph >= 9
    if alkaline:
        ings = [
            ingredient("Sodium hydroxide", 6.0),
            ingredient("Sodium metasilicate", 8.0),
            ingredient("Sodium tripolyphosphate", 5.0),
            ingredient("Sodium gluconate", 2.0),
            ingredient("Nonionic surfactant (C12-14 EO7)", 4.0),
            ingredient("Deionized water", 75.0),
        ]
        name = "Alkaline soak degreaser (baseline)"
        rationale = "Alkaline builder system with silicate corrosion protection and nonionic surfactant for emulsifying mineral oils."
    else:
        ings = [
            ingredient("D-Limonene", 18.0),
            ingredient("Nonionic surfactant (C12-14 EO7)", 8.0),
            ingredient("Butyl glycol", 6.0),
            ingredient("Deionized water", 68.0),
        ]
        name = "Solvent-emulsion degreaser (baseline)"
        rationale = "Microemulsion of d-limonene with nonionic surfactant and coupling solvent for near-neutral cleaning."
    return _balanced(name, ProductDomain.degreaser, ings, rationale)


def _surface_treatment_template(req: Requirement) -> Formulation:
    if req.substrate in (Substrate.aluminum, Substrate.magnesium_alloy):
        ings = [
            ingredient("Hexafluorozirconic acid", 1.2),
            ingredient("(3-Aminopropyl)triethoxysilane (APTES)", 0.8),
            ingredient("Cerium nitrate", 0.5),
            ingredient("Deionized water", 97.5),
        ]
        name = "Zr/silane conversion coating (baseline)"
        rationale = "Chrome-free Zr-based conversion with silane film former and cerium inhibitor for light metals."
    else:
        ings = [
            ingredient("Phosphoric acid", 8.0),
            ingredient("Zinc oxide", 3.0),
            ingredient("Manganese dihydrogen phosphate", 4.0),
            ingredient("Sodium nitrite", 0.3),
            ingredient("Deionized water", 84.7),
        ]
        name = "Zinc phosphate conversion (baseline)"
        rationale = "Zinc/manganese phosphating bath with nitrite accelerator for steel pretreatment."
    return _balanced(name, ProductDomain.surface_treatment, ings, rationale)


def _balanced(name: str, domain: ProductDomain, ings: list[Ingredient], rationale: str) -> Formulation:
    """Normalise weight percentages to sum to 100 by adjusting the solvent."""
    total = sum(i.weight_pct for i in ings)
    if abs(total - 100.0) > 1e-6 and ings:
        # Adjust the largest solvent/water component to absorb the difference.
        solvent_idx = max(
            range(len(ings)),
            key=lambda k: (ings[k].role in ("solvent",), ings[k].weight_pct),
        )
        ings[solvent_idx].weight_pct = round(ings[solvent_idx].weight_pct + (100.0 - total), 4)
    return Formulation(name=name, domain=domain, ingredients=ings, rationale=rationale)


TEMPLATE_BUILDERS = {
    ProductDomain.anticorrosion_coating: _anticorrosion_template,
    ProductDomain.degreaser: _degreaser_template,
    ProductDomain.surface_treatment: _surface_treatment_template,
}


def baseline_formulation(req: Requirement) -> Formulation:
    return TEMPLATE_BUILDERS[req.domain](req)


def variant_formulations(req: Requirement, n: int = 3) -> list[Formulation]:
    """Produce n distinct baseline variants by perturbing key levers."""
    base = baseline_formulation(req)
    variants = [base]
    levers = {
        ProductDomain.anticorrosion_coating: ("Zinc phosphate", [1.4, 0.7]),
        ProductDomain.degreaser: ("Nonionic surfactant (C12-14 EO7)", [1.5, 0.6]),
        ProductDomain.surface_treatment: ("Phosphoric acid", [1.3, 0.75]),
    }
    target_role, factors = levers[req.domain]
    labels = ["high-active", "lean"]
    for factor, label in zip(factors, labels):
        ings = [ing.model_copy(deep=True) for ing in base.ingredients]
        for ing in ings:
            if ing.name == target_role or (target_role not in [i.name for i in ings] and ing.role == "inhibitor"):
                ing.weight_pct = round(ing.weight_pct * factor, 4)
        f = _balanced(f"{base.name.split(' (')[0]} ({label})", req.domain, ings, base.rationale)
        variants.append(f)
    return variants[:n]


MECHANISMS = {
    ProductDomain.anticorrosion_coating: (
        "Barrier + active protection: the cross-linked epoxy/acrylic network forms a low-permeability "
        "barrier, while zinc phosphate releases phosphate ions that passivate the steel surface, forming "
        "insoluble iron/zinc phosphate complexes at coating defects. Cross-link density (resin:hardener "
        "stoichiometry) governs water uptake and thus salt-spray endurance."
    ),
    ProductDomain.degreaser: (
        "Saponification + emulsification: alkaline builders hydrolyse fatty soils into soluble soaps, "
        "silicates buffer pH and protect the substrate, and nonionic surfactants below the cloud point "
        "lower interfacial tension to emulsify mineral oils and lift particulate soils (roll-up mechanism)."
    ),
    ProductDomain.surface_treatment: (
        "Conversion film growth: acid attack micro-etches the metal, raising interfacial pH and "
        "precipitating an insoluble phosphate/oxide-fluoride film that is chemically bonded to the substrate, "
        "increasing surface area and providing anchoring and corrosion-inhibiting sites for subsequent coats."
    ),
}
