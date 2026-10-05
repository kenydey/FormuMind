"""One Morgan-fingerprint helper, no deprecated RDKit calls left (round-4).

``AllChem.GetMorganFingerprintAsBitVect`` logged "DEPRECATION WARNING: please use MorganGenerator" on every call —
dozens per recommendation — and disappears when RDKit drops it. The generator API yields the identical bit vector,
so similarity scores and thresholds stay where they were.
"""
from __future__ import annotations

from pathlib import Path

import pytest

rdkit = pytest.importorskip("rdkit")

from rdkit import Chem  # noqa: E402

from app.services.fingerprints import morgan_bitvect  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app"

SMILES = [
    "CCO",
    "c1ccccc1O",
    "CC(C)(c1ccc(O)cc1)c1ccc(O)cc1",  # bisphenol A
    "[O-]P(=O)([O-])[O-].[Zn+2]",  # zinc phosphate
    "N[C@@H](C)C(=O)O",
    "N[C@H](C)C(=O)O",
]


@pytest.mark.parametrize("smiles", SMILES)
def test_the_helper_gives_the_bits_the_deprecated_call_gave(smiles):
    import warnings

    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        legacy = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048)
    assert list(morgan_bitvect(mol).GetOnBits()) == list(legacy.GetOnBits())


def test_radius_and_size_are_honoured():
    mol = Chem.MolFromSmiles("CC(C)(c1ccc(O)cc1)c1ccc(O)cc1")
    assert morgan_bitvect(mol, radius=1, n_bits=512).GetNumBits() == 512
    assert list(morgan_bitvect(mol, radius=1).GetOnBits()) != list(morgan_bitvect(mol, radius=3).GetOnBits())


def test_nothing_in_app_calls_the_deprecated_function_except_the_helpers_fallback():
    offenders = [
        f"{path.relative_to(APP.parent)}:{n}"
        for path in sorted(APP.rglob("*.py"))
        if path.name != "fingerprints.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "GetMorganFingerprintAsBitVect" in line
    ]
    assert not offenders, f"use app.services.fingerprints.morgan_bitvect: {offenders}"
