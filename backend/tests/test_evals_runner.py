"""The evaluation runner and report (``backend/evals/run.py``, ``report.py``): the headline numbers, the comparison with a stored
baseline, the CLI, and the committed baseline file itself."""
from __future__ import annotations

import copy
import json

import pytest
from evals import report as R
from evals import run as runner

# ── a small canned report, shaped like the suites' output ─────────────────────────────────────────


def _overall(**values):
    return {name: {"mean": v, "ci_low": v, "ci_high": v, "n": 10} for name, v in values.items()}


def _system(s1, ndcg):
    return {
        "overall": _overall(**{"success@1": s1, "success@5": 0.9, "recall@10": 0.8, "mrr@10": 0.8, "ndcg@10": ndcg}),
        "by_category": {"lexical": {"success@1": 0.95, "n": 5}, "paraphrase": {"success@5": 0.9, "n": 5}},
    }


def _retrieval(s1=0.7, ndcg=0.75):
    """The production retriever moves with the arguments; the references stay where they were."""
    return {
        "suite": "retrieval",
        "systems": {"production": _system(s1, ndcg), "bm25_reference": _system(0.7, 0.75), "random": _system(0.01, 0.05)},
        "abstention": {"production": {"auroc": 0.5}},
    }


def _report(**kwargs):
    return {"meta": {"git": "abc123", "python": "3.11", "seconds": 1.0}, "suites": {"retrieval": _retrieval(**kwargs)}}


def test_the_headline_names_every_tracked_number_with_its_direction_and_tolerance():
    metrics = R.headline(_report())
    assert metrics["retrieval/production/success@1"] == R.Metric("retrieval/production/success@1", 0.7, "higher", 0.03)
    assert "retrieval/production/lexical/success@1" in metrics and "retrieval/abstention/production/auroc" in metrics
    assert metrics["retrieval/production_minus_bm25_reference/ndcg@10"].value == 0.0
    assert all(m.direction in (R.HIGHER, R.LOWER) and m.tolerance > 0 for m in metrics.values())


def test_a_number_a_suite_could_not_compute_is_left_out_not_invented():
    report = _report()
    report["suites"]["retrieval"]["abstention"]["production"]["auroc"] = None
    assert "retrieval/abstention/production/auroc" not in R.headline(report)


def test_optimisation_regret_is_a_lower_is_better_number():
    report = {"suites": {"optimization": {"summary": {"0.0": {"optuna-tpe": {"mean_final_regret": 0.08, "mean_advantage_over_random": 0.1}}}}}}
    metrics = R.headline(report)
    assert metrics["optimization/noise0.0/optuna-tpe/final_regret"].direction == R.LOWER
    assert metrics["optimization/noise0.0/optuna-tpe/advantage_over_random"].direction == R.HIGHER
    assert metrics["optimization/noise0.0/optuna-tpe/final_regret"].tolerance == R.TOLERANCE["optimization"]


def _baseline(**overrides):
    metrics = R.headline(_report())
    for name, value in overrides.items():
        metrics[name] = R.Metric(name, value, metrics[name].direction, metrics[name].tolerance)
    return R.to_baseline(metrics)


def test_a_number_that_got_worse_beyond_tolerance_is_a_regression_in_either_direction():
    now = R.headline(_report(s1=0.60))  # was 0.70
    comparison = R.compare(now, _baseline())
    assert [d.name for d in comparison.regressions] == ["retrieval/production/success@1"]
    assert comparison.regressions[0].worse_by == pytest.approx(0.10)
    # a lower-is-better number going up is just as much a regression
    base = {"metrics": {"optimization/x": {"value": 0.10, "direction": "lower", "tolerance": 0.05}}}
    worse = {"optimization/x": R.Metric("optimization/x", 0.20, "lower", 0.05)}
    better = {"optimization/x": R.Metric("optimization/x", 0.01, "lower", 0.05)}
    assert [d.name for d in R.compare(worse, base).regressions] == ["optimization/x"]
    assert [d.name for d in R.compare(better, base).improvements] == ["optimization/x"]


def test_movement_inside_the_tolerance_is_unchanged_and_better_is_an_improvement():
    assert R.compare(R.headline(_report(s1=0.69)), _baseline()).regressions == []
    comparison = R.compare(R.headline(_report(s1=0.80)), _baseline())
    assert [d.name for d in comparison.improvements] == ["retrieval/production/success@1"] and comparison.regressions == []
    assert comparison.unchanged == len(R.headline(_report())) - 1  # only success@1 moved


def test_new_and_missing_numbers_are_reported_not_compared():
    baseline = _baseline()
    baseline["metrics"]["retrieval/production/old_metric"] = {"value": 0.5, "direction": "higher", "tolerance": 0.03}
    del baseline["metrics"]["retrieval/production/mrr@10"]
    comparison = R.compare(R.headline(_report()), baseline)
    assert comparison.missing == ["retrieval/production/old_metric"] and comparison.new == ["retrieval/production/mrr@10"]


def test_the_baseline_decides_direction_and_tolerance_not_the_current_run():
    base = {"metrics": {"retrieval/production/success@1": {"value": 0.7, "direction": "higher", "tolerance": 0.2}}}
    assert R.compare(R.headline(_report(s1=0.55)), base).regressions == []  # 0.15 < the baseline's 0.2


def test_the_markdown_lists_what_moved_and_folds_the_rest():
    metrics = R.headline(_report(s1=0.60))
    text = R.markdown(_report(s1=0.60), metrics, R.compare(metrics, _baseline()))
    assert "### Regressions" in text and "`retrieval/production/success@1` | 0.700 | 0.600" in text
    assert "<details><summary>retrieval" in text and "abc123" in text
    assert "No baseline" in R.markdown(_report(), metrics, None)


# ── the CLI, with the suites replaced by a canned report ──────────────────────────────────────────


def _run(monkeypatch, tmp_path, report, *argv):
    monkeypatch.setattr(runner, "run_suites", lambda names, **kwargs: report)
    return runner.main(["--suite", "retrieval", "--baseline", str(tmp_path / "baseline.json"), *argv])


def test_update_baseline_then_a_worse_run_fails_only_when_asked(monkeypatch, tmp_path, capsys):
    out, md = tmp_path / "report.json", tmp_path / "summary.md"
    assert _run(monkeypatch, tmp_path, _report(), "--update-baseline", "--out", str(out), "--markdown", str(md)) == 0
    stored = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    assert stored["metrics"]["retrieval/production/success@1"] == {"value": 0.7, "direction": "higher", "tolerance": 0.03}
    assert json.loads(out.read_text(encoding="utf-8"))["suites"]["retrieval"]["suite"] == "retrieval"
    assert md.read_text(encoding="utf-8").startswith("## Evaluation report")

    assert _run(monkeypatch, tmp_path, _report(s1=0.5)) == 0  # reported, not enforced
    assert "WORSE   retrieval/production/success@1" in capsys.readouterr().out
    assert _run(monkeypatch, tmp_path, _report(s1=0.5), "--fail-on-regression") == 1
    assert _run(monkeypatch, tmp_path, _report(s1=0.9), "--fail-on-regression") == 0  # better is fine


def test_updating_the_baseline_keeps_the_suites_that_were_not_run(monkeypatch, tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"metrics": {"parsing/production/mean_score": {"value": 0.9, "direction": "higher", "tolerance": 0.03}}}), encoding="utf-8")
    assert _run(monkeypatch, tmp_path, _report(), "--update-baseline") == 0
    stored = json.loads(path.read_text(encoding="utf-8"))["metrics"]
    assert "parsing/production/mean_score" in stored and "retrieval/production/success@1" in stored


def test_a_partial_run_does_not_call_the_other_suites_missing(monkeypatch, tmp_path, capsys):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"metrics": {"parsing/production/mean_score": {"value": 0.9, "direction": "higher", "tolerance": 0.03}}}), encoding="utf-8")
    assert _run(monkeypatch, tmp_path, _report()) == 0
    assert "0 missing" in capsys.readouterr().out


def test_quick_runs_are_not_compared_on_optimisation_numbers(monkeypatch, tmp_path, capsys):
    report = {"meta": {}, "suites": {"optimization": {"summary": {"0.0": {"optuna-tpe": {"mean_final_regret": 0.9}}}}}}
    (tmp_path / "baseline.json").write_text(json.dumps({"metrics": {"optimization/noise0.0/optuna-tpe/final_regret": {"value": 0.1, "direction": "lower", "tolerance": 0.05}}}), encoding="utf-8")
    monkeypatch.setattr(runner, "run_suites", lambda names, **kwargs: report)
    assert runner.main(["--suite", "optimization", "--quick", "--baseline", str(tmp_path / "baseline.json"), "--fail-on-regression"]) == 0
    assert "final_regret" not in capsys.readouterr().out


def test_an_unknown_suite_is_refused_and_an_answerer_spec_must_name_a_function():
    with pytest.raises(SystemExit):
        runner.main(["--suite", "nonsense"])
    with pytest.raises(SystemExit):
        runner.load_answerer("not-a-spec")
    assert runner.load_answerer("evals.suites.qa:declined") is not None


# ── the committed baseline ────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def baseline():
    return json.loads(runner.BASELINE.read_text(encoding="utf-8"))


def test_the_committed_baseline_is_well_formed(baseline):
    metrics = baseline["metrics"]
    assert len(metrics) >= 40
    for name, entry in metrics.items():
        assert set(entry) == {"value", "direction", "tolerance"} and entry["direction"] in (R.HIGHER, R.LOWER), name
        assert isinstance(entry["value"], (int, float)) and entry["tolerance"] > 0, name
    assert {name.split("/", 1)[0] for name in metrics} == set(runner.SUITES)
    assert "git" in baseline["meta"] and "libraries" in baseline["meta"]


def test_every_number_a_run_tracks_is_in_the_baseline(baseline):
    """Adding a metric without refreshing the baseline would leave it untracked forever: fail here instead.

    A run in a smaller environment (no PDF generator, no Optuna) produces fewer numbers, never other ones.
    """
    report = runner.run_suites(["retrieval", "qa", "parsing", "optimization"], quick=True)
    tracked = {name for name in R.headline(report) if not name.startswith("optimization/")}
    assert tracked <= set(baseline["metrics"]), sorted(tracked - set(baseline["metrics"]))
    engines = {"numpy-ucb", "optuna-tpe", "halton", "random"}
    optimisation = {n for n in R.headline(report) if n.startswith("optimization/noise0.0/") and n.split("/")[2] in engines}
    assert optimisation <= set(baseline["metrics"]), sorted(optimisation - set(baseline["metrics"]))
