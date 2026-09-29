#!/usr/bin/env python3
"""W6-1 · P1-38: 严谨性 rubric 回归门禁（默认开启，纯本地、无 LLM 成本）。

在 ``app/resources/golden_rigor.py`` 的每组 golden 问答上跑
``evaluate_rigor``，按 ``config.evals_rigor_thresholds`` 判定：

* exit 0 — 全部指标均值达标；
* exit 1 — 有指标均值低于阈值（打印失败明细）；
* exit 2 — 基础设施异常（import 失败 / 全部 pair 评估出错）。

对抗用例（``"adversarial": True`` 的 pair）在独立章节单独计分：
报告每个 trap 被新对抗指标检出（FLAGGED）还是漏网（MISSED），
任一 trap 漏网即 exit 1（硬门禁），不污染 32 组标准集的通过线。

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
    # 对抗集单独计分：不参与 32 组通过线判定，只报告 trap 检出情况。
    adv_metric_names = ("abstention_correctness", "contradiction_flagged", "value_correctness")
    standard_pairs = [p for p in golden_rigor_pairs if not p.get("adversarial")]
    adversarial_pairs = [p for p in golden_rigor_pairs if p.get("adversarial")]
    sums = {m: 0.0 for m in metric_names}
    counts = {m: 0 for m in metric_names}
    errors: list[str] = []
    failed_pairs: list[str] = []

    for i, pair in enumerate(standard_pairs):
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
    # 趋势追踪（只记录、不影响门禁判定；仅统计标准集）
    _record_history(means, dict(counts), len(standard_pairs))
    if errors:
        print(f"warnings: {len(errors)} evaluation errors (skipped):")
        for e in errors[:10]:
            print(f"  ! {e}")
    if failed_pairs:
        print(f"failed pairs: {len(failed_pairs)}")
    print(
        f"rigor_gate: {'PASS' if overall_ok and not failed_pairs else 'FAIL'} "
        f"({len(standard_pairs)} standard pairs)"
    )
    main_rc = 0 if (overall_ok and not failed_pairs) else 1

    # ---- 对抗集：单独计分，trap 漏网影响 exit code（硬门禁） ----
    missed_traps: list[str] = []
    if adversarial_pairs:
        print("=" * 60)
        print(f"adversarial set: {len(adversarial_pairs)} trap pairs (HARD GATE)")
        detected = {m: 0 for m in adv_metric_names}
        applicable = {m: 0 for m in adv_metric_names}
        for i, pair in enumerate(adversarial_pairs):
            label = pair.get("question", f"adv[{i}]")[:40]
            try:
                result = evaluate_rigor(
                    pair.get("answer", ""),
                    pair.get("evidence", []),
                    pair.get("key_claims", []),
                    thresholds=thresholds,
                    pair_meta=pair,
                )
            except Exception as exc:
                print(f"[adv{i}] {label}: evaluate raised {type(exc).__name__}: {exc}")
                continue
            parts: list[str] = []
            for m in adv_metric_names:
                mr = result[m]
                if mr.get("not_applicable"):
                    continue
                applicable[m] += 1
                # trap 被指标打 0 分 = 成功检出；得 1 分 = 漏网
                if mr.get("score") == 0.0:
                    detected[m] += 1
                    parts.append(f"{m}=FLAGGED")
                else:
                    missed_traps.append(f"[adv{i}] {m}")
                    parts.append(f"{m}=MISSED(score={mr.get('score')})")
                for f in mr["failures"]:
                    parts.append(f"    - {f['reason']}")
            # 旧指标在 trap 上的表现（纵深防御参考）
            old_fired = [
                m for m in metric_names
                if result[m].get("score") is not None and result[m]["score"] < thresholds.get(m, 1.0)
            ]
            if old_fired:
                parts.append(f"[old metrics also fired: {','.join(old_fired)}]")
            print(f"[adv{i}] {label}: {'; '.join(parts) if parts else 'no applicable adversarial metric'}")
        print("-" * 60)
        for m in adv_metric_names:
            if applicable[m]:
                print(
                    f"{m}: detected {detected[m]}/{applicable[m]} traps"
                )
        if missed_traps:
            print(f"ADVERSARIAL GATE FAILED: {len(missed_traps)} trap(s) missed:")
            for t in missed_traps:
                print(f"  - {t}")
            main_rc = 1
    return main_rc


if __name__ == "__main__":
    raise SystemExit(main())
