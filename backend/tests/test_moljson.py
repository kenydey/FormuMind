"""Tests for SMILES ↔ MolJSON conversion (P0 of the MolJSON plan)."""

import pytest

from app.services.moljson import (
    format_moljson,
    moljson_to_smiles,
    rdkit_available,
    smiles_to_moljson,
    validate_smiles,
)

pytestmark = pytest.mark.skipif(
    not rdkit_available(), reason="RDKit not importable in this environment"
)

# (name, smiles, expected atom count, expected ring count)
# Expected values are RDKit-computed (GetNumAtoms / GetSSSR) — do not
# hand-count aromatic shorthand; RDKit is the source of truth.
KNOWN_MOLECULES = [
    ("DGEBA", "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1", 25, 4),
    ("IPDA", "CC1(C)CC(N)CC(C)(CN)C1", 12, 1),
    ("2-Mercaptobenzothiazole", "c1ccc2c(c1)nc(s2)S", 10, 2),
    ("Xylene", "Cc1ccccc1C", 8, 1),
    ("Water", "O", 1, 0),
    ("Butyl glycol", "CCCCOCCO", 8, 0),
]

INVALID_SMILES = ["", "   ", "not-a-molecule", "C1=CC=CC", "C(("]


class TestSmilesToMoljson:
    def test_known_molecules_atoms_and_bonds(self):
        for name, smiles, n_atoms, n_rings in KNOWN_MOLECULES:
            mj = smiles_to_moljson(smiles)
            assert mj is not None, f"{name}: conversion failed"
            assert len(mj["atoms"]) == n_atoms, f"{name}: atom count {len(mj['atoms'])} != {n_atoms}"
            # Single-atom molecules (e.g. water) legitimately have no bonds.
            # Bond endpoints reference existing atoms.
            for b in mj["bonds"]:
                assert 1 <= b["a"] <= n_atoms, f"{name}: bond a out of range"
                assert 1 <= b["b"] <= n_atoms, f"{name}: bond b out of range"
                assert b["order"] >= 1, f"{name}: bad bond order"

    def test_ring_counts_match_sssr(self):
        for name, smiles, _, n_rings in KNOWN_MOLECULES:
            info = validate_smiles(smiles)
            assert info["valid"], f"{name}: validation failed"
            assert info["ring_count"] == n_rings, f"{name}: rings {info['ring_count']} != {n_rings}"

    def test_invalid_smiles_return_none(self):
        for bad in INVALID_SMILES:
            assert smiles_to_moljson(bad) is None, f"expected None for {bad!r}"
            assert validate_smiles(bad)["valid"] is False, f"expected invalid for {bad!r}"


class TestRoundTrip:
    def test_roundtrip_known_molecules(self):
        for name, smiles, _, _ in KNOWN_MOLECULES:
            mj = smiles_to_moljson(smiles)
            assert mj is not None
            back = moljson_to_smiles(mj)
            assert back is not None, f"{name}: roundtrip failed"
            info = validate_smiles(smiles)
            assert info["roundtrip_ok"], f"{name}: roundtrip mismatch {back} != {info['smiles']}"

    def test_format_is_compact_json(self):
        mj = smiles_to_moljson("O")
        assert mj is not None
        s = format_moljson(mj)
        assert s.startswith("{")
        assert "," in s  # compact separators
        assert "\n" not in s  # single line


class TestValidateSmiles:
    def test_validate_returns_full_report(self):
        info = validate_smiles("CCO")
        assert info["valid"] is True
        assert info["smiles"] == "CCO"
        assert info["moljson"] is not None
        assert info["roundtrip_ok"] is True
        assert info["atom_count"] == 3
        assert info["ring_count"] == 0


class TestMoljsonMeta:
    """M-A: computed-properties enrichment."""

    def test_meta_dgeba(self):
        from app.services.moljson import moljson_meta

        meta = moljson_meta("CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1")
        assert meta is not None
        assert meta["formula"] == "C21H24O4"
        assert meta["heavy_atoms"] == 25
        assert meta["total_rings"] == 4
        assert meta["aromatic_rings"] == 2

    def test_meta_invalid_none(self):
        from app.services.moljson import moljson_meta

        assert moljson_meta("not-a-molecule") is None
        assert moljson_meta("") is None


class TestDetectFunctionalGroups:
    """M-C: SMARTS functional-group detection."""

    def test_dgeba_groups(self):
        from app.services.moljson import detect_functional_groups

        g = detect_functional_groups("CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1")
        assert "epoxy" in g
        assert "aromatic_ring" in g

    def test_ipda_groups(self):
        from app.services.moljson import detect_functional_groups

        g = detect_functional_groups("CC1(C)CC(N)CC(C)(CN)C1")
        assert "amine_primary" in g
        assert "epoxy" not in g

    def test_invalid_empty(self):
        from app.services.moljson import detect_functional_groups

        assert detect_functional_groups("not-a-molecule") == []
        assert detect_functional_groups("") == []
