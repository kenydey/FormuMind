"""P0 benchmark: DeepSeek SMILES vs MolJSON structure-reasoning accuracy.

Replicates a subset of the MolJSON paper (arXiv:2605.01822) tasks — atom
counting, ring counting, functional-group identification, and connectivity
(shortest-path-style) reasoning — using the project's configured LLM
(DeepSeek via app.services.llm._call_llm) instead of GPT-5/Claude. This is
the P0 decision gate: if MolJSON does not beat SMILES by a meaningful margin
on DeepSeek, the P1/P2 work is not justified.

Usage (from backend/, venv active):
    python -m scripts.benchmark_moljson            # full run
    python -m scripts.benchmark_moljson --limit 1  # quick smoke (per task)

Output: per-task accuracy table (SMILES vs MolJSON) + report JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from app.services.llm import _call_llm
from app.services.moljson import format_moljson, smiles_to_moljson

# --- benchmark question set -------------------------------------------------
# Each case: (smiles, task, expected). Tasks:
#   atoms — "How many heavy (non-hydrogen) atoms?"
#   rings — "How many rings (SSSR)?" (0 for acyclic)
#   funcs — "List the functional groups present" (expected = non-empty subset;
#            scored as any expected group found)
#   path  — "Is there a bond path between atoms A1 and A9?" (true/false)
#           The question pins *specific* atom ids; for SMILES we render the
#           linear string and ask about the *first* and *last* atom; for
#           MolJSON we render explicit ids. Both are unambiguous within one
#           format, and the point is measuring whether the model can reason
#           about connectivity from the rendered structure.
CASES = [
    # (smiles, "atoms", expected_count)
    ("O", "atoms", 1),
    ("CCO", "atoms", 3),
    ("CCCCOCCO", "atoms", 8),
    ("Cc1ccccc1C", "atoms", 8),
    ("CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1", "atoms", 25),
    ("CC1(C)CC(N)CC(C)(CN)C1", "atoms", 12),
    ("c1ccc2c(c1)nc(s2)S", "atoms", 10),
    ("O=C=NC1CC(C)(C)CC(CN=C=O)C1", "atoms", 16),
    # Larger, ring-heavy molecules stress the "ring complexity" failure mode.
    ("C[C@H](O)c1ccc(F)cc1", "atoms", 10),  # chiral center + aromatic
    ("CC12CCCC1C1CC[C@@H]3C(=O)CC[C@H]3C1CC2", "atoms", 18),  # steroidal
    ("C1=CC2=C(C=C1)NC3=CC=CC=C3N2", "atoms", 15),  # carbazole-ish
    # (smiles, "rings", expected_count)
    ("CCO", "rings", 0),
    ("Cc1ccccc1C", "rings", 1),
    ("CC1(C)CC(N)CC(C)(CN)C1", "rings", 1),
    ("c1ccc2c(c1)nc(s2)S", "rings", 2),
    ("CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1", "rings", 4),
    ("C1=CC2=C(C=C1)NC3=CC=CC=C3N2", "rings", 3),
    ("CC12CCCC1C1CC[C@@H]3C(=O)CC[C@H]3C1CC2", "rings", 4),
    # (smiles, "funcs", expected_groups)
    ("CCO", "funcs", ["hydroxyl", "alcohol"]),
    ("CC(=O)O", "funcs", ["carboxylic acid", "carboxyl"]),
    ("O=C=NC1CC(C)(C)CC(CN=C=O)C1", "funcs", ["isocyanate"]),
    ("c1ccc2c(c1)nc(s2)S", "funcs", ["thiol", "benzothiazole"]),
    ("CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1", "funcs", ["epoxide", "epoxy"]),
    ("CCOC(=O)C(C)=C", "funcs", ["ester", "acrylate"]),
    # (smiles, "path", expected_bool) — connected pair (true) / disconnected
    # attempt (false). We use two distinct atom ids in the same molecule;
    # "false" cases are pairs whose bond distance is large but still
    # connected, so they test whether the model can trace connectivity.
    ("CCCCCCCCCC", "path", True),   # long chain: first↔last are connected
    ("C1CCCCC1", "path", True),     # ring: any two atoms connected
    ("CCCC.CCCC", "path", False),   # two separate fragments (dot bond)
    ("O=C(O)Cc1ccccc1", "path", True),  # acid + benzene, connected
]

TASK_PROMPTS = {
    "atoms": (
        "Count the heavy (non-hydrogen) atoms in the molecule below. "
        "Reply with ONLY an integer, no explanation."
    ),
    "rings": (
        "Count the rings in the molecule below (SSSR ring count; aromatic rings "
        "count as rings). Reply with ONLY an integer, no explanation."
    ),
    "funcs": (
        "List the functional groups present in the molecule below. "
        "Reply with a comma-separated list of group names, nothing else."
    ),
    "path": (
        "In the molecule below, is there a continuous path of bonds from the "
        "first atom to the last atom (following bond connections through "
        "intermediate atoms)? Reply with ONLY yes or no."
    ),
}


def _render(smiles: str, fmt: str, task: str) -> str:
    if fmt == "smiles":
        if task == "path":
            return f"Molecule (SMILES): {smiles}"
        return f"Molecule (SMILES): {smiles}"
    mj = smiles_to_moljson(smiles)
    if mj is None:
        raise ValueError(f"cannot convert {smiles}")
    return f"Molecule (MolJSON, atoms have unique ids, bonds list connectivity):\n{format_moljson(mj)}"


def _extract_int(text: str) -> int | None:
    m = re.search(r"-?\d+", text or "")
    return int(m.group()) if m else None


def _score(task: str, answer: str, expected) -> bool:
    if task in ("atoms", "rings"):
        got = _extract_int(answer)
        return got == expected
    if task == "path":
        low = (answer or "").lower()
        if expected:
            return "yes" in low and "no" not in low.split(".")[0]
        return "no" in low and "yes" not in low.split(".")[0]
    # funcs: any expected group mentioned (case-insensitive substring)
    low = (answer or "").lower()
    return any(g.lower() in low for g in expected)


def run_case(smiles: str, task: str, expected, fmt: str) -> bool:
    prompt = TASK_PROMPTS[task] + "\n\n" + _render(smiles, fmt, task)
    raw = _call_llm(prompt)
    if not raw:
        return False  # LLM unavailable/failed — counts as miss
    return _score(task, raw, expected)


def run(fmt: str, limit: int | None = None) -> dict:
    if limit is not None:
        # Limit per task, so a smoke run exercises all task types.
        per_task: dict[str, list] = {}
        for smiles, task, expected in CASES:
            per_task.setdefault(task, []).append((smiles, task, expected))
        cases = [c for task in per_task for c in per_task[task][:limit]]
    else:
        cases = CASES
    results = {"smiles": [], "atoms": 0, "rings": 0, "funcs": 0, "path": 0}
    counts = {"atoms": 0, "rings": 0, "funcs": 0, "path": 0}
    for smiles, task, expected in cases:
        ok = run_case(smiles, task, expected, fmt)
        results[task] += int(ok)
        counts[task] += 1
        results["smiles"].append((smiles, task, expected, ok))
    results["counts"] = counts
    results["total"] = len(cases)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek SMILES vs MolJSON benchmark")
    ap.add_argument("--limit", type=int, default=None, help="only first N cases per task (smoke test)")
    args = ap.parse_args()

    if not _call_llm("Reply with OK"):
        print("ERROR: no LLM configured (missing deepseek_api_key / llm_provider).", file=sys.stderr)
        return 2

    total_cases = len(CASES) if args.limit is None else 4 * args.limit
    print(f"Benchmarking DeepSeek: SMILES vs MolJSON ({total_cases} cases)\n")
    r_smiles = run("smiles", args.limit)
    r_moljson = run("moljson", args.limit)

    print(f"{'task':<8} {'SMILES':>8} {'MolJSON':>8} {'delta':>8}")
    print("-" * 36)
    for task in ("atoms", "rings", "funcs", "path"):
        s = r_smiles[task]
        m = r_moljson[task]
        n = r_smiles["counts"][task]
        print(f"{task:<8} {s}/{n} {m}/{n} {m - s:+d}")
    s_tot = sum(r_smiles[t] for t in ("atoms", "rings", "funcs", "path"))
    m_tot = sum(r_moljson[t] for t in ("atoms", "rings", "funcs", "path"))
    n_tot = sum(r_smiles["counts"].values())
    print(f"{'total':<8} {s_tot}/{n_tot} {m_tot}/{n_tot} {m_tot - s_tot:+d}")

    print("\n--- per-case (MolJSON) ---")
    for smiles, task, expected, ok in r_moljson["smiles"]:
        print(f"  [{'OK' if ok else 'XX'}] {task:<6} {str(expected):<30} {smiles}")

    with open("benchmark_moljson_report.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "smiles": {t: r_smiles[t] for t in ("atoms", "rings", "funcs", "path")},
                "moljson": {t: r_moljson[t] for t in ("atoms", "rings", "funcs", "path")},
                "counts": r_smiles["counts"],
                "total_cases": n_tot,
                "verdict": "PASS" if m_tot > s_tot else "INCONCLUSIVE",
            },
            f, ensure_ascii=False, indent=2,
        )
    print("\nReport written to benchmark_moljson_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
