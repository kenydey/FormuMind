"""P2: genome DiscreteExcludeConstraint builders (baybe optional)."""
from __future__ import annotations

from app.services.engines.adapters.baybe_space_builder import (
    discrete_exclude_constraints_for_pools,
    high_freq_exclude_pair_sets,
)


def test_high_freq_pair_sets_cover_acid_rules():
    pairs = high_freq_exclude_pair_sets()
    assert len(pairs) >= 3
    flat = " ".join(h for left, right in pairs for h in left + right)
    assert "zinc dust" in flat
    assert "acid" in flat
    assert "carbonate" in flat


def test_discrete_exclude_empty_without_two_mat_slots():
    assert discrete_exclude_constraints_for_pools([]) == []
    assert discrete_exclude_constraints_for_pools(
        [("mat_resin", ["Bisphenol-A epoxy (DGEBA)", "Acrylic resin"])]
    ) == []


def test_discrete_exclude_builds_when_baybe_available():
    pools = [
        ("mat_pigment", ["Zinc dust", "Titanium dioxide", "Iron oxide"]),
        ("mat_acid", ["Phosphoric acid", "Acetic acid", "Water"]),
    ]
    try:
        from baybe.constraints import DiscreteExcludeConstraint  # noqa: F401
    except Exception:
        # Fail-open: no baybe → empty list (same as production degrade).
        assert discrete_exclude_constraints_for_pools(pools) == []
        return

    constraints = discrete_exclude_constraints_for_pools(pools)
    assert constraints, "expected at least one metal×acid exclude"
    assert all(type(c).__name__ == "DiscreteExcludeConstraint" for c in constraints)
    # Zinc dust × Phosphoric/Acetic should appear.
    params = {tuple(c.parameters) for c in constraints}
    assert ("mat_pigment", "mat_acid") in params or ("mat_acid", "mat_pigment") in params


def test_discrete_exclude_skips_unrelated_pools():
    pools = [
        ("mat_resin", ["Bisphenol-A epoxy (DGEBA)", "Acrylic resin"]),
        ("mat_solvent", ["Water", "Ethanol"]),
    ]
    # No metal/acid/carbonate/alkali hits → no excludes (even with baybe).
    assert discrete_exclude_constraints_for_pools(pools) == []
