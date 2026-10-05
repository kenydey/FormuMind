"""Morgan fingerprints through RDKit's generator API.

``AllChem.GetMorganFingerprintAsBitVect`` is deprecated: every call logged
"DEPRECATION WARNING: please use MorganGenerator" (dozens per recommendation, from the structure / KG / chemtools
similarity paths) and goes away when RDKit removes it. The generator produces the identical bit vector
(radius 2, 2048 bits — checked on the platform's own material SMILES), so ranking and thresholds do not move.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=8)
def _generator(radius: int, n_bits: int) -> Any:
    from rdkit.Chem import rdFingerprintGenerator

    return rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)


def morgan_bitvect(mol: Any, radius: int = 2, n_bits: int = 2048) -> Any:
    """Morgan bit-vector fingerprint of an RDKit ``Mol`` (ECFP4 by default)."""
    try:
        return _generator(radius, n_bits).GetFingerprint(mol)
    except (ImportError, AttributeError):  # RDKit older than the generator API
        from rdkit.Chem import AllChem

        return AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)
