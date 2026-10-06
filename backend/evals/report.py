"""Headline numbers of a report, their comparison with a stored baseline, and the Markdown summary.

A run produces a large nested result per suite. What is tracked over time is a flat list of named numbers - the *headline*
- each with the direction that is better and how much movement is noise. The baseline file stores exactly those, so a change
is a difference between two small dictionaries rather than a diff of two reports.

Tolerances are absolute and deliberately plain: the retrieval, QA and parsing suites are deterministic (fixed corpus, fixed
documents, no sampling), so any movement is a change in behaviour, and 0.03 only keeps a one-query flip in a nine-query
category from being called a regression of the system. The optimisation suite samples (seeded, so repeatable for one library
version, but a new Optuna release changes its sampler), hence 0.05.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

HIGHER, LOWER = "higher", "lower"
TOLERANCE = {"retrieval": 0.03, "qa": 0.03, "parsing": 0.03, "optimization": 0.05}

_RETRIEVAL_OVERALL = ("success@1", "success@5", "recall@10", "mrr@10", "ndcg@10")
_RETRIEVAL_BY_CATEGORY = {
    "lexical": "success@1",
    "numeric": "success@1",
    "hard_negative": "success@1",
    "multi_doc": "success@1",
    "paraphrase": "success@5",
    "crosslingual": "success@5",
}
_QA_METRICS = ("fact_recall@1", "fact_recall@3", "fact_recall@5", "full_context@5", "context_precision@5")


@dataclass(frozen=True)
class Metric:
    name: str
    value: float
    direction: str
    tolerance: float


@dataclass(frozen=True)
class Delta:
    name: str
    baseline: float
    current: float
    direction: str
    tolerance: float

    @property
    def change(self) -> float:
        return self.current - self.baseline

    @property
    def worse_by(self) -> float:
        """How far the number moved in the bad direction (negative: it improved)."""
        return -self.change if self.direction == HIGHER else self.change


@dataclass
class Comparison:
    regressions: list[Delta]
    improvements: list[Delta]
    unchanged: int
    new: list[str]
    missing: list[str]


def _add(out: dict[str, Metric], suite: str, name: str, value, direction: str) -> None:
    if value is None or value != value:  # a suite that could not compute a number leaves it out rather than invent one
        return
    out[name] = Metric(name, round(float(value), 4), direction, TOLERANCE[suite])


def headline(report: dict) -> dict[str, Metric]:
    """The tracked numbers of a report, by name (``suite/system/...``)."""
    out: dict[str, Metric] = {}
    suites = report.get("suites", {})

    retrieval = suites.get("retrieval")
    if retrieval:
        for system in ("production", "bm25_reference"):
            data = retrieval["systems"].get(system)
            if not data:
                continue
            for metric in _RETRIEVAL_OVERALL:
                _add(out, "retrieval", f"retrieval/{system}/{metric}", data["overall"][metric]["mean"], HIGHER)
            if system == "production":
                for category, metric in _RETRIEVAL_BY_CATEGORY.items():
                    row = data["by_category"].get(category)
                    if row:
                        _add(out, "retrieval", f"retrieval/production/{category}/{metric}", row[metric], HIGHER)
        auroc = retrieval["abstention"].get("production", {}).get("auroc")
        _add(out, "retrieval", "retrieval/abstention/production/auroc", auroc, HIGHER)
        production, reference = retrieval["systems"].get("production"), retrieval["systems"].get("bm25_reference")
        if production and reference:
            gap = production["overall"]["ndcg@10"]["mean"] - reference["overall"]["ndcg@10"]["mean"]
            _add(out, "retrieval", "retrieval/production_minus_bm25_reference/ndcg@10", gap, HIGHER)

    qa = suites.get("qa")
    if qa:
        for system in ("production", "bm25_reference"):
            data = qa["systems"].get(system)
            if not data:
                continue
            for metric in _QA_METRICS:
                _add(out, "qa", f"qa/{system}/{metric}", data["overall"][metric]["mean"], HIGHER)
            gate = data["no_evidence_gate"]
            _add(out, "qa", f"qa/{system}/gate/refuses_unanswerable", gate["refuses_unanswerable"], HIGHER)
            _add(out, "qa", f"qa/{system}/gate/starves_answerable", gate["starves_answerable"], LOWER)
        answers = qa.get("answers")
        if answers:
            _add(out, "qa", "qa/answers/answer_fact_recall", answers["answer_fact_recall"]["mean"], HIGHER)
            _add(out, "qa", "qa/answers/answers_answerable", answers["answers_answerable"], HIGHER)
            _add(out, "qa", "qa/answers/declines_unanswerable", answers["declines_unanswerable"], HIGHER)

    parsing = suites.get("parsing")
    if parsing:
        for system in ("production", "naive"):
            data = parsing["systems"].get(system)
            if not data:
                continue
            _add(out, "parsing", f"parsing/{system}/mean_score", data["overall"]["mean_score"], HIGHER)
            _add(out, "parsing", f"parsing/{system}/pass_rate", data["overall"]["pass_rate"], HIGHER)
            if system == "production":
                for kind, row in data["by_kind"].items():
                    _add(out, "parsing", f"parsing/production/kind/{kind}", row["mean_score"], HIGHER)
                for ext, row in data["by_ext"].items():
                    _add(out, "parsing", f"parsing/production/format/{ext}", row["mean_score"], HIGHER)

    optimization = suites.get("optimization")
    if optimization:
        for level, engines in optimization["summary"].items():
            for engine, row in engines.items():
                _add(out, "optimization", f"optimization/noise{level}/{engine}/final_regret", row["mean_final_regret"], LOWER)
                if "mean_advantage_over_random" in row:
                    _add(out, "optimization", f"optimization/noise{level}/{engine}/advantage_over_random",
                         row["mean_advantage_over_random"], HIGHER)
    return out


def to_baseline(metrics: dict[str, Metric], meta: dict | None = None) -> dict:
    return {
        "meta": meta or {},
        "metrics": {m.name: {"value": m.value, "direction": m.direction, "tolerance": m.tolerance} for m in sorted(metrics.values(), key=lambda m: m.name)},
    }


def compare(current: dict[str, Metric], baseline: dict) -> Comparison:
    """Current numbers against a baseline's; the baseline's direction and tolerance decide what counts as a move."""
    stored = baseline.get("metrics", {})
    regressions: list[Delta] = []
    improvements: list[Delta] = []
    unchanged = 0
    for name, metric in sorted(current.items()):
        base = stored.get(name)
        if base is None:
            continue
        delta = Delta(name, float(base["value"]), metric.value, base.get("direction", metric.direction), float(base.get("tolerance", metric.tolerance)))
        if delta.worse_by > delta.tolerance:
            regressions.append(delta)
        elif delta.worse_by < -delta.tolerance:
            improvements.append(delta)
        else:
            unchanged += 1
    return Comparison(
        regressions=regressions,
        improvements=improvements,
        unchanged=unchanged,
        new=sorted(set(current) - set(stored)),
        missing=sorted(set(stored) - set(current)),
    )


# ── Markdown ─────────────────────────────────────────────────────────────────────────────────────


def _row(delta: Delta) -> str:
    arrow = "▲" if delta.change > 0 else "▼"
    return f"| `{delta.name}` | {delta.baseline:.3f} | {delta.current:.3f} | {arrow} {delta.change:+.3f} |"


def markdown(report: dict, metrics: dict[str, Metric], comparison: Comparison | None) -> str:
    lines = ["## Evaluation report", ""]
    meta = report.get("meta", {})
    if meta:
        lines += [f"`{meta.get('git', '?')}` · python {meta.get('python', '?')} · {meta.get('seconds', '?')} s", ""]
    if comparison is None:
        lines += ["No baseline to compare with.", ""]
    else:
        lines += [
            f"**{len(metrics)} tracked numbers**: {len(comparison.regressions)} worse than the baseline beyond tolerance, "
            f"{len(comparison.improvements)} better, {comparison.unchanged} unchanged, "
            f"{len(comparison.new)} new, {len(comparison.missing)} not produced this run.",
            "",
        ]
        for title, deltas in (("Regressions", comparison.regressions), ("Improvements", comparison.improvements)):
            if deltas:
                lines += [f"### {title}", "", "| metric | baseline | now | change |", "|---|---|---|---|", *map(_row, deltas), ""]
        if comparison.new:
            lines += ["New (not in the baseline yet): " + ", ".join(f"`{n}`" for n in comparison.new[:12]) + (" …" if len(comparison.new) > 12 else ""), ""]
    grouped: dict[str, list[Metric]] = defaultdict(list)
    for metric in metrics.values():
        grouped[metric.name.split("/", 1)[0]].append(metric)
    for suite, rows in grouped.items():
        lines += [f"<details><summary>{suite} ({len(rows)})</summary>", "", "| metric | value |", "|---|---|"]
        lines += [f"| `{m.name}` | {m.value:.3f} |" for m in sorted(rows, key=lambda m: m.name)]
        lines += ["", "</details>", ""]
    return "\n".join(lines)
