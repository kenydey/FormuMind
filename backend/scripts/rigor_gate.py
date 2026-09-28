#!/usr/bin/env python3
"""W6-1 · P1-38: 严谨性 rubric 回归门禁（默认开启，纯本地、无 LLM 成本）。

在 ``app/resources/golden_rigor.py`` 的每组 golden 问答上跑
``evaluate_rigor``，按 ``config.evals_rigor_thresholds`` 判定：

* exit 0 — 全部指标均值达标；
* exit 1 — 有指标均值低于阈值（打印失败明细）；
* exit 2 — 基础设施异常（import 失败 / 全部 pair 评估出错）。

单 pair 评估异常记 error 并跳过（fail-open），不直接炸门禁；
可用 ``FORMUMIND_RIGOR_GATE=0`` 跳过（默认开启）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


# 趋势追踪：每次运行把三项均值 append 到 backend/evals-history.jsonl
# （gitignore，不入库）。相对上次退化超 0.15 只打印 WARNING，不 fail 门禁。
_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evals-history.jsonl",
)
_REGRESSION_WARN_DELTA = 0.15


def _load_last_history() -> dict | None:
    try:
        with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
            last = None
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except ValueError:
                    continue
            return last
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"rigor_gate: history read failed: {exc}", file=sys.stderr)
        return None


def _record_history(means: dict[str, float], counts: dict[str, int], n_pairs: int) -> None:
    prev = _load_last_history()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "means": means,
        "counts": counts,
        "pairs": n_pairs,
    }
    try:
        with open(_HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"rigor_gate: history write failed: {exc}", file=sys.stderr)
        return
    if isinstance(prev, dict):
        old_means = prev.get("means") or {}
        for name, mean in means.items():
            old = old_means.get(name)
            if isinstance(old, (int, float)) and old - mean > _REGRESSION_WARN_DELTA:
                print(
                    f"WARNING: rigor metric '{name}' regressed "
                    f"{old:.4f} -> {mean:.4f} (drop > {_REGRESSION_WARN_DELTA})"
                )


def _skip_requested() -> bool:
    return (os.environ.get("FORMUMIND_RIGOR_GATE") or "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def main() -> int:
    if _skip_requested():
        print("rigor_gate: skipped (FORMUMIND_RIGOR_GATE=0)")
        return 0

    try:
        from app.config import get_settings
        from app.evals.rigor_rubric import evaluate_rigor
        from app.resources.golden_rigor import golden_rigor_pairs
    except Exception as exc:
        print(f"rigor_gate: import failed: {exc}", file=sys.stderr)
        return 2

    try:
        thresholds = dict(get_settings().evals_rigor_thresholds or {})
    except Exception as exc:
        print(f"rigor_gate: cannot load settings: {exc}", file=sys.stderr)
        return 2

    metric_names = ("citation_veracity", "coverage", "numeric_consistency")
    sums = {m: 0.0 for m in metric_names}
    counts = {m: 0 for m in metric_names}
    errors: list[str] = []
    failed_pairs: list[str] = []

    for i, pair in enumerate(golden_rigor_pairs):
        label = pair.get("question", f"pair[{i}]")[:40]
        try:
            result = evaluate_rigor(
                pair.get("answer", ""),
                pair.get("evidence", []),
                pair.get("key_claims", []),
                thresholds=thresholds,
            )
        except Exception as exc:  # fail-open：记 error，继续下一组
            errors.append(f"[{label}] evaluate raised {type(exc).__name__}: {exc}")
            continue
        pair_ok = True
        detail: list[str] = []
        for m in metric_names:
            mr = result[m]
            if mr.get("score") is None:
                errors.append(f"[{label}] {m} error: {mr.get('error')}")
                continue
            sums[m] += mr["score"]
            counts[m] += 1
            th = thresholds.get(m, 1.0)
            mark = "ok" if mr["score"] >= th else "FAIL"
            if mark == "FAIL":
                pair_ok = False
            detail.append(f"{m}={mr['score']:.3f}(>={th})[{mark}]")
            for f in mr["failures"]:
                detail.append(f"    - {f['claim']}: {f['reason']}")
        print(f"[{i}] {label}: {'; '.join(detail)}")
        if not pair_ok:
            failed_pairs.append(label)

    print("-" * 60)
    overall_ok = True
    means: dict[str, float] = {}
    for m in metric_names:
        if counts[m] == 0:
            print(f"{m}: no valid evaluations (all errored)")
            overall_ok = False
            continue
        mean = sums[m] / counts[m]
        means[m] = mean
        th = thresholds.get(m, 1.0)
        ok = mean >= th
        overall_ok = overall_ok and ok
        print(
            f"{m}: mean={mean:.4f} over {counts[m]} pairs, "
            f"threshold={th} -> {'PASS' if ok else 'FAIL'}"
        )
    # 趋势追踪（只记录、不影响门禁判定）
    _record_history(means, dict(counts), len(golden_rigor_pairs))
    if errors:
        print(f"warnings: {len(errors)} evaluation errors (skipped):")
        for e in errors[:10]:
            print(f"  ! {e}")
    if failed_pairs:
        print(f"failed pairs: {len(failed_pairs)}")
    print(
        f"rigor_gate: {'PASS' if overall_ok and not failed_pairs else 'FAIL'} "
        f"({len(golden_rigor_pairs)} pairs)"
    )
    return 0 if (overall_ok and not failed_pairs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
