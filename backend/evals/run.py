"""``python -m evals``: run the suites, write the report, compare with the baseline.

    python -m evals                                   # every suite, print the headline numbers
    python -m evals --suite retrieval,qa              # some of them
    python -m evals --out report.json --markdown $GITHUB_STEP_SUMMARY --baseline evals/baselines/baseline.json
    python -m evals --update-baseline                 # after a change you meant to make: store the new numbers
    python -m evals --suite optimization --engines botorch-ei   # the slow engine (a GP fit per suggestion), on its own
    python -m evals --suite qa --answerer mypkg.mod:answer   # also score real answers (needs a model; see evals/README.md)

Exit status is 0 unless ``--fail-on-regression`` is given and a tracked number is worse than the baseline beyond its tolerance.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from . import report as R

SUITES = ("retrieval", "qa", "parsing", "optimization")
BASELINE = Path(__file__).resolve().parent / "baselines" / "baseline.json"
QUICK_OPTIMIZATION = {"budget": 15, "seeds": 3, "noise": (0.0,)}
LIBRARIES = ("jieba", "rank-bm25", "numpy", "optuna", "botorch", "markitdown", "pymupdf4llm", "PyMuPDF", "trafilatura",
             "openpyxl", "python-docx", "fpdf2", "sentence-transformers")


def _versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in LIBRARIES:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return found


def _git() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10, cwd=Path(__file__).parent)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def load_answerer(spec: str) -> Callable[[str, list[str]], str]:
    """``package.module:function`` -> the function."""
    module, _, name = spec.partition(":")
    if not module or not name:
        raise SystemExit(f"--answerer wants 'package.module:function', got {spec!r}")
    return getattr(importlib.import_module(module), name)


def run_suites(names: Sequence[str], *, quick: bool = False, answerer: Callable | None = None,
               engine_names: Sequence[str] | None = None) -> dict:
    from .suites import optimization, parsing, qa, retrieval

    started = time.monotonic()
    suites: dict[str, dict] = {}
    collected = retrieval.collect() if {"retrieval", "qa"} & set(names) else None  # the corpus is ingested once for both
    if "retrieval" in names:
        suites["retrieval"] = retrieval.evaluate(collected)
    if "qa" in names:
        suites["qa"] = qa.run(collected=collected, answerer=answerer)
    if "parsing" in names:
        suites["parsing"] = parsing.run()
    if "optimization" in names:
        suites["optimization"] = optimization.run(**(QUICK_OPTIMIZATION if quick else {}), engine_names=engine_names)
    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": _git(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "libraries": _versions(),
        "quick": quick,
        "seconds": round(time.monotonic() - started, 1),
    }
    return {"meta": meta, "suites": suites}


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m evals", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", default="all", help=f"comma-separated subset of {', '.join(SUITES)} (default: all)")
    ap.add_argument("--quick", action="store_true", help="a smaller optimisation study (15 runs, 3 seeds, no noise); its numbers are not compared with the baseline")
    ap.add_argument("--out", type=Path, help="write the full report (JSON) here")
    ap.add_argument("--markdown", type=Path, help="append a Markdown summary here (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--baseline", type=Path, default=BASELINE, help="baseline to compare with (default: evals/baselines/baseline.json)")
    ap.add_argument("--update-baseline", action="store_true", help="store this run's numbers as the baseline (suites not run keep theirs)")
    ap.add_argument("--fail-on-regression", action="store_true")
    ap.add_argument("--engines", help="optimisers to study, comma-separated (default: the fast ones installed; name botorch-ei to include it - minutes)")
    ap.add_argument("--answerer", help="package.module:function(question, snippets) -> answer text, to score answers as well (qa suite)")
    return ap.parse_args(argv)


def _selected(spec: str) -> list[str]:
    names = list(SUITES) if spec == "all" else [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in SUITES]
    if unknown:
        raise SystemExit(f"unknown suite(s): {', '.join(unknown)} (choose from {', '.join(SUITES)})")
    return names


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    names = _selected(args.suite)
    answerer = load_answerer(args.answerer) if args.answerer else None
    engine_names = [e.strip() for e in args.engines.split(",") if e.strip()] if args.engines else None
    report = run_suites(names, quick=args.quick, answerer=answerer, engine_names=engine_names)
    metrics = R.headline(report)
    if args.quick:  # a smaller study has other numbers: comparing them with the full study's baseline would be noise
        metrics = {k: v for k, v in metrics.items() if not k.startswith("optimization/")}

    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.is_file() else None
    comparison = R.compare(metrics, baseline) if baseline else None
    # the baseline only speaks for suites that ran: a partial run must not report the others as "not produced"
    if comparison:
        comparison.missing = [m for m in comparison.missing if m.split("/", 1)[0] in report["suites"] and not (args.quick and m.startswith("optimization/"))]

    for name, metric in sorted(metrics.items()):
        print(f"{name:70s} {metric.value:8.3f}")
    if comparison:
        print(f"\nvs baseline: {len(comparison.regressions)} worse, {len(comparison.improvements)} better, {comparison.unchanged} unchanged, "
              f"{len(comparison.new)} new, {len(comparison.missing)} missing")
        for d in comparison.regressions:
            print(f"  WORSE   {d.name}: {d.baseline:.3f} -> {d.current:.3f} (tolerance {d.tolerance})")
        for d in comparison.improvements:
            print(f"  better  {d.name}: {d.baseline:.3f} -> {d.current:.3f}")
    elif not args.update_baseline:
        print("\nno baseline at", args.baseline)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.markdown:
        with args.markdown.open("a", encoding="utf-8") as handle:
            handle.write(R.markdown(report, metrics, comparison) + "\n")
    if args.update_baseline:
        stored = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.is_file() else {"metrics": {}}
        merged = {k: v for k, v in stored.get("metrics", {}).items() if k.split("/", 1)[0] not in report["suites"]}
        merged.update(R.to_baseline(metrics)["metrics"])
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        payload = {"meta": {**report["meta"], "note": "regenerate with: python -m evals --update-baseline"}, "metrics": dict(sorted(merged.items()))}
        args.baseline.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"baseline written: {args.baseline} ({len(merged)} numbers)")
    return 1 if (args.fail_on_regression and comparison and comparison.regressions) else 0


if __name__ == "__main__":
    sys.exit(main())
