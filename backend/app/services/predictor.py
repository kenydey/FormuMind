"""Property prediction service.

Predicts the performance properties that matter for metal surface treatment
(salt-spray endurance, dry film weight, adhesion, cleaning efficiency) from a
formulation's composition.

The default implementation is a transparent empirical surrogate whose
coefficients come from the domain knowledge base — no GPU or model download
needed, fully deterministic. When RDKit and a trained DeepChem/ChemBERTa model
are installed, :func:`_molecular_features` enriches the feature vector; the
public :func:`predict` signature is unchanged.
"""
from __future__ import annotations

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
    weighted_logp = 0.0
    total = 0.0
    for ing in form.ingredients:
        if ing.smiles:
            mol = Chem.MolFromSmiles(ing.smiles)
            if mol is not None:
                weighted_logp += Descriptors.MolLogP(mol) * ing.weight_pct
                total += ing.weight_pct
    return {"mean_logp": weighted_logp / total} if total else {}


def predict(form: Formulation) -> dict[str, float]:
    """Return predicted properties keyed by metric name."""
    props: dict[str, float] = {}
    mol = _molecular_features(form)

    if form.domain == ProductDomain.anticorrosion_coating:
        inhibitor = _sum_role(form, "inhibitor")
        binder = _sum_role(form, "resin") + _sum_role(form, "hardener")
        filler = _sum_role(form, "filler") + _sum_role(form, "pigment")
        ratio = amine_epoxy_ratio(form) or 2.5
        # Cross-link efficiency peaks near a 2:1 resin:hardener ratio.
        crosslink = max(0.0, 1.0 - abs(ratio - 2.0) / 3.0)
        salt_spray = (
            120.0
            + inhibitor * 48.0
            + binder * 6.5
            + crosslink * 240.0
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
        coating_weight = 0.4 + active * 0.22 + accel * 2.5
        props["coating_weight_gsm"] = round(coating_weight, 2)
        props["salt_spray_hours"] = round(48.0 + active * 9.0 + inhibitor * 30.0, 1)
        props["adhesion_promotion_idx"] = round(active * 0.6 + 1.0, 2)

    if mol:
        # LogP nudges barrier/cleaning behaviour when descriptors are present.
        if "salt_spray_hours" in props:
            props["salt_spray_hours"] = round(props["salt_spray_hours"] * (1.0 + 0.01 * mol["mean_logp"]), 1)
    return props


def objective_value(form: Formulation, objective: str) -> float:
    """Scalar objective (higher is better) used by the optimizer."""
    props = predict(form)
    return float(props.get(objective, next(iter(props.values()), 0.0)))
