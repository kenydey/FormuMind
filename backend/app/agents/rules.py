"""Deterministic rule tables for the Chemist Agent.

These encode the hard chemical-compatibility knowledge the agent enforces. They
are data-driven (and extensible) so the agent logic stays declarative.
"""
from __future__ import annotations

# SMARTS for a free isocyanate group (N=C=O). RDKit matches this against an
# ingredient's SMILES; a hit in a waterborne system means the NCO will hydrolyse.
FREE_ISOCYANATE_SMARTS = "[NX2]=[CX2]=[OX1]"

# Materials that require a solvent carrier and must be intercepted in waterborne
# systems even when the knowledge base carries no explicit ``carrier`` field.
# This guarantees the must-intercept behaviour for the canonical examples even
# offline / without RDKit.
SOLVENT_ONLY_NAMES = {
    "Desmodur BL 3175",
    "Blocked isocyanate (IPDI)",
}

# Recommended waterborne replacements keyed by the offending ingredient's role.
# Values are real RAW_MATERIALS keys so a downstream substitution resolves.
WATERBORNE_ALTERNATIVES: dict[str, list[str]] = {
    "hardener": [
        "Waterborne polyisocyanate (hydrophilic HDI)",
        "Polyamide hardener",
    ],
    "resin": [
        "Polyurethane dispersion",
        "Waterborne acrylic emulsion",
    ],
    "accelerator": [
        "Bismuth neodecanoate",
    ],
}

# Tin catalyst commonly paired with isocyanate cures; flagged for replacement in
# waterborne systems where bismuth/zinc catalysts are preferred.
TIN_CATALYST_NAME = "Dibutyltin dilaurate (DBTL)"
WATERBORNE_CATALYST = "Bismuth neodecanoate"

# Map a remediation to its Recommendation.kind by the offending role.
KIND_BY_ROLE = {
    "hardener": "substitute_crosslinker",
    "resin": "substitute_resin",
    "accelerator": "swap_catalyst",
}
