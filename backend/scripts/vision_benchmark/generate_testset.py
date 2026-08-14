"""Generate a chemical-structure test set for vision-LLM SMILES benchmarks.

Draws 2D structure images from ground-truth SMILES with RDKit, writes
``images/*.png`` and ``ground_truth.json``.  Compounds span the difficulty
gradient and the autodeposition-coating chemistry the user works in
(acrylates, isocyanates/polyurethane, bisphenol-A epoxy, acid phosphate).
"""
from __future__ import annotations

import json
import os

from rdkit import Chem
from rdkit.Chem import AllChem, Draw, rdMolDescriptors

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")

# name -> SMILES. Difficulty tiers marked in the comment.
TEST_SMILES = [
    # ── tier 1: 简单 (直链 / 单取代苯 / 单官能团) ─────────────────
    ("ethanol", "CCO"),
    ("toluene", "Cc1ccccc1"),
    ("styrene", "C=Cc1ccccc1"),
    ("phenol", "Oc1ccccc1"),
    ("aniline", "Nc1ccccc1"),
    # ── tier 2: 中等 (单环 + 官能团 / 双官能团) ───────────────────
    ("benzoic_acid", "OC(=O)c1ccccc1"),
    ("terephthalic_acid", "OC(=O)c1ccc(C(=O)O)cc1"),
    ("adipic_acid", "OC(=O)CCCCC(=O)O"),
    ("cyclohexanone", "O=C1CCCCC1"),
    ("caprolactam", "O=C1CCCCCN1"),
    ("mma", "CC(=C)C(=O)OC"),
    ("bisphenol_a", "CC(C)(c1ccc(O)cc1)c1ccc(O)cc1"),
    # ── tier 3: 困难 (多官能团 / 多环 / 杂环) ─────────────────────
    ("tdi", "Cc1ccc(N=C=O)cc1N=C=O"),
    ("mdi", "O=C=Nc1ccc(Cc2ccc(N=C=O)cc2)cc1"),
    ("ipdi", "CC1(C)CC(N=C=O)CC(C)(CN=C=O)C1"),
    ("caffeine", "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"),
    ("naphthalene", "c1ccc2ccccc2c1"),
    ("anthracene", "c1ccc2cc3ccccc3cc2c1"),
    ("aspirin", "CC(=O)Oc1ccccc1C(=O)O"),
    ("citric_acid", "OC(=O)CC(O)(CC(=O)O)C(=O)O"),
]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    records = []
    for name, smi in TEST_SMILES:
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            raise SystemExit(f"bad SMILES for {name}: {smi}")
        canonical = Chem.MolToSmiles(mol)
        formula = rdMolDescriptors.CalcMolFormula(mol)
        AllChem.Compute2DCoords(mol)
        path = os.path.join(OUT, f"{name}.png")
        Draw.MolToFile(mol, path, size=(500, 500), fitImage=True)
        records.append(
            {"name": name, "smiles": canonical, "formula": formula, "image": path}
        )
        print(f"  {name:<18} {canonical:<40} {formula}")

    gt_path = os.path.join(HERE, "ground_truth.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"\n生成 {len(records)} 张结构图 → {OUT}")
    print(f"ground truth → {gt_path}")


if __name__ == "__main__":
    main()
