"""Parsing: does the structure a file carries - tables, merged cells, reading order, units - survive the parser?

Each case in ``evals/parsing_cases.py`` is a small real file with a ground truth. The production entry point
(``parsing.parse_document``, whichever tier of the chain wins) is read against the repository's own last-resort tiers, the ones
used when nothing better is installed: python-docx paragraphs (which drop every table), the openpyxl sheet dump, pypdf's
plain text extraction and a tag-stripper. "Production below the last resort on a case" means a regression or a missing extra.

Scores are per case, from the metrics its ground truth defines (none that it does not):

* ``cell_recall`` - table cells present in the output at all;
* ``row_integrity`` - table rows whose cells still share a line (a flattened table keeps its text and loses its meaning);
* ``order`` - fragments in reading order (longest increasing subsequence of their positions);
* ``number_fidelity`` - figures that survive digit for digit, as whole tokens;
* ``unit_fidelity`` - value and unit still adjacent (blanks aside: ``50µm`` is ``50 µm``);
* ``noise_free`` - what must be absent is absent, and a running header appears at most once.

The case score is the mean of its defined metrics; ``pass_rate`` is the share of cases scoring 1.0.
"""
from __future__ import annotations

import importlib
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .. import metrics as M
from ..parsing_cases import Case, Truth, cases as all_cases

_NUMERIC = re.compile(r"[+\-±]?\d[\d.,]*")
_PLAIN_WORD = re.compile(r"[a-z0-9 ]+")
METRICS = ("cell_recall", "row_integrity", "order", "number_fidelity", "unit_fidelity", "noise_free")


def _norm(text: str) -> str:
    return M.normalise_cell(text)


def has_fragment(haystack_norm: str, fragment: str) -> bool:
    """Whether ``fragment`` is in the (normalised) text. A figure must be a whole token: ``4.5`` is not in ``14.5``."""
    needle = _norm(fragment)
    if not needle:
        return True
    if _NUMERIC.fullmatch(needle):
        return re.search(rf"(?<![\w.]){re.escape(needle)}(?!\w|\.\d)", haystack_norm) is not None
    return needle in haystack_norm


def count_fragment(haystack_norm: str, fragment: str) -> int:
    needle = _norm(fragment)
    if not needle:
        return 0
    if _PLAIN_WORD.fullmatch(needle):  # "nan" must not be found inside "finance"
        return len(re.findall(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack_norm))
    return haystack_norm.count(needle)


def _share(hits: int, total: int) -> float | None:
    return None if total == 0 else hits / total


def score_output(output: str, truth: Truth) -> dict[str, float | None]:
    """The per-metric scores of one parse (None where the case's ground truth defines no such check)."""
    text = _norm(output)
    lines = [_norm(line) for line in output.splitlines()]

    cell_recall = _share(sum(has_fragment(text, c) for c in truth.cells), len(truth.cells))
    row_hits = 0
    for row in truth.rows:
        cells = [c for c in row if c.strip()]
        row_hits += any(all(has_fragment(line, c) for c in cells) for line in lines)
    row_integrity = _share(row_hits, len(truth.rows))
    order = M.contains_in_order(output, truth.order) if truth.order else None
    numbers = _share(sum(has_fragment(text, n) for n in truth.numbers), len(truth.numbers))

    squeezed = re.sub(r"\s+", "", text)
    unit_hits = sum(re.sub(r"\s+", "", _norm(u)) in squeezed for u in truth.units)
    units = _share(unit_hits, len(truth.units))

    violations = sum(count_fragment(text, a) > 0 for a in truth.absent) + sum(count_fragment(text, r) > 1 for r in truth.repeats)
    noise_free = None if not (truth.absent or truth.repeats) else 1 - violations / (len(truth.absent) + len(truth.repeats))
    return {
        "cell_recall": cell_recall,
        "row_integrity": row_integrity,
        "order": order,
        "number_fidelity": numbers,
        "unit_fidelity": units,
        "noise_free": noise_free,
    }


def case_score(metrics: dict[str, float | None]) -> float:
    defined = [v for v in metrics.values() if v is not None]
    return sum(defined) / len(defined) if defined else 0.0


# ── the systems ──────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Parsed:
    text: str
    parser: str


Parser = Callable[[bytes, str], Parsed]


def production_parser(content: bytes, ext: str) -> Parsed:
    from app.services import parsing

    result = parsing.parse_document(content, ext)
    return Parsed(result.markdown or "", result.parser)


def naive_parser(content: bytes, ext: str) -> Parsed:
    """The last-resort tier for the format: what the application falls back to when nothing better is installed."""
    from app.services import parsing

    if ext == "docx":
        text = parsing._parse_docx(content)
    elif ext == "xlsx":
        text = parsing._parse_xlsx(content)
    elif ext == "pdf":
        text = parsing._parse_pypdf(content)
    else:
        text = re.sub(r"<(script|style)\b.*?</\1>|<[^>]+>", " ", content.decode("utf-8", "replace"), flags=re.S | re.I)
    return Parsed(text or "", f"naive-{ext}")


def _missing(case: Case) -> str | None:
    for module in case.needs:
        try:
            importlib.import_module(module)
        except ImportError:
            return f"generator needs {module}"
    return None


def _aggregate(rows: list[dict]) -> dict:
    by_metric: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for name in METRICS:
            if row["metrics"][name] is not None:
                by_metric[name].append(row["metrics"][name])
    scores = [row["score"] for row in rows]
    return {
        "cases": len(rows),
        "mean_score": round(M.mean(scores), 4) if scores else None,
        "pass_rate": round(sum(s >= 1.0 for s in scores) / len(scores), 4) if scores else None,
        "metrics": {name: round(M.mean(values), 4) for name, values in by_metric.items()},
    }


def run(*, systems: dict[str, Parser] | None = None, cases: list[Case] | None = None) -> dict:
    systems = systems or {"production": production_parser, "naive": naive_parser}
    chosen = list(cases if cases is not None else all_cases())
    per_system: dict[str, list[dict]] = {name: [] for name in systems}
    detail: list[dict] = []
    skipped: list[dict] = []
    for case in chosen:
        reason = _missing(case)
        if reason:
            skipped.append({"id": case.id, "reason": reason})
            continue
        content = case.build()
        entry: dict = {"id": case.id, "kind": case.kind, "ext": case.ext, "note": case.note}
        for name, parse in systems.items():
            parsed = parse(content, case.ext)
            metrics = score_output(parsed.text, case.truth)
            row = {"id": case.id, "kind": case.kind, "ext": case.ext, "metrics": metrics, "score": round(case_score(metrics), 4)}
            per_system[name].append(row)
            entry[name] = {"parser": parsed.parser, "score": row["score"], "metrics": {k: v for k, v in metrics.items() if v is not None}}
        detail.append(entry)

    summary: dict[str, dict] = {}
    for name, rows in per_system.items():
        by_kind: dict[str, list[dict]] = defaultdict(list)
        by_ext: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_kind[row["kind"]].append(row)
            by_ext[row["ext"]].append(row)
        summary[name] = {
            "overall": _aggregate(rows),
            "by_kind": {k: _aggregate(v) for k, v in sorted(by_kind.items())},
            "by_ext": {k: _aggregate(v) for k, v in sorted(by_ext.items())},
        }
    return {"suite": "parsing", "systems": summary, "cases": detail, "skipped": skipped}
