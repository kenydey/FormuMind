"""SMILES ↔ MolJSON conversion with RDKit round-trip validation.

MolJSON is the explicit-graph molecular representation proposed by the
Oxford OXPIG group (arXiv:2605.01822): a JSON object with explicit atom
lists ({id, element}) and bond lists ({a, b, order}). It is designed for
LLM consumption — SMILES linearises a graph into a traversal and IUPAC
names encode it through nomenclature rules, both of which cause systematic
LLM errors (atom miscounts, ring complexity); MolJSON flattens the graph
explicitly and is compatible with LLM structured-output modes.

This module is a pure-function utility: no task logic, no I/O. It feeds
(1) a MolScribe recognition-result validation loop and (2) a DeepSeek
benchmark comparing SMILES vs MolJSON as LLM inputs (P0 of the MolJSON
plan — docs/plans/moljson-structure-reasoning.md).
"""

from __future__ import annotations

import json
from typing import Any

try:  # RDKit is a hard dep of the backend image (chemtools uses it).
    from rdkit import Chem
except Exception:  # pragma: no cover - import guard for exotic environments
    Chem = None  # type: ignore[assignment]


def rdkit_available() -> bool:
    return Chem is not None


def smiles_to_moljson(smiles: str) -> dict[str, Any] | None:
    """Convert a SMILES string to MolJSON.

    Returns ``{atoms: [{id, element}], bonds: [{a, b, order}]}`` with
    1-based atom ids, or None when RDKit cannot parse the SMILES (invalid
    structure, not a molecule, or RDKit unavailable).
    """
    if not rdkit_available() or not smiles or not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    atoms = []
    for atom in mol.GetAtoms():
        atoms.append({"id": atom.GetIdx() + 1, "element": atom.GetSymbol()})
    bonds = []
    for bond in mol.GetBonds():
        order = bond.GetBondTypeAsDouble()
        # Aromatic bonds report 1.5 — keep the fractional value so the
        # round-trip rebuild can restore BondType.AROMATIC instead of
        # collapsing to a double bond (which breaks ring valences).
        bonds.append(
            {
                "a": bond.GetBeginAtomIdx() + 1,
                "b": bond.GetEndAtomIdx() + 1,
                "order": order,
            }
        )
    return {"atoms": atoms, "bonds": bonds}


def moljson_to_smiles(moljson: dict[str, Any]) -> str | None:
    """Convert MolJSON back to canonical SMILES (round-trip check).

    Returns None when the structure is invalid or RDKit is unavailable.
    Uses explicit hydrogens removed then canonical SMILES, so the output is
    directly comparable to the original input.
    """
    if not rdkit_available():
        return None
    try:
        mol = Chem.RWMol()
        for atom in moljson.get("atoms", []):
            mol.AddAtom(Chem.Atom(atom.get("element", "C")))
        for bond in moljson.get("bonds", []):
            mol.AddBond(
                int(bond["a"]) - 1,
                int(bond["b"]) - 1,
                _bond_type(float(bond["order"])),
            )
        # Sanitize: MolFromSmiles-level check so invalid valences fail here.
        Chem.SanitizeMol(mol)
        return Chem.MolToSmiles(mol)
    except Exception:
        return None


def _bond_type(order: float):
    if order <= 1.0:
        return Chem.BondType.SINGLE
    if order == 1.5:
        return Chem.BondType.AROMATIC
    if order == 2.0:
        return Chem.BondType.DOUBLE
    if order == 3.0:
        return Chem.BondType.TRIPLE
    return Chem.BondType.SINGLE


def validate_smiles(smiles: str) -> dict[str, Any]:
    """Validate a SMILES string and report structure fidelity.

    Returns a dict with:
      valid          - RDKit parses the SMILES
      smiles         - canonical form (or input when invalid)
      moljson        - MolJSON structure (None when invalid)
      roundtrip_ok   - MolJSON→SMILES equals the canonical input
      atom_count     - atom count from the graph (0 when invalid)
      ring_count     - ring count via SSSR (0 when invalid)
    Used by the MolScribe-result validation loop and the LLM-output gate.
    """
    stripped = (smiles or "").strip()
    if not rdkit_available() or not stripped:
        return {"valid": False, "smiles": smiles, "moljson": None,
                "roundtrip_ok": False, "atom_count": 0, "ring_count": 0}
    raw = Chem.MolFromSmiles(stripped)
    canonical = Chem.MolToSmiles(raw) if raw is not None else None
    if canonical is None:
        return {"valid": False, "smiles": smiles, "moljson": None,
                "roundtrip_ok": False, "atom_count": 0, "ring_count": 0}
    moljson = smiles_to_moljson(canonical)
    rt = moljson_to_smiles(moljson) if moljson else None
    mol = Chem.MolFromSmiles(canonical)
    rings = Chem.GetSSSR(mol) if mol is not None else ()
    return {
        "valid": True,
        "smiles": canonical,
        "moljson": moljson,
        "roundtrip_ok": bool(rt) and rt == canonical,
        "atom_count": mol.GetNumAtoms() if mol is not None else 0,
        "ring_count": len(rings) if mol is not None else 0,
    }


def format_moljson(moljson: dict[str, Any]) -> str:
    """Compact one-line JSON for prompt embedding (saves tokens)."""
    return json.dumps(moljson, ensure_ascii=False, separators=(",", ":"))
