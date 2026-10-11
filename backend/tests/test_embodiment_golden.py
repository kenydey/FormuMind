"""v2 P0-8: 专利实施例表 golden 集回归测试。

基线：tests/golden/embodiment_tables.json（20 表，中英双语、多种单位）。
指标：组分名检出率（name recall）/ wt% 归一化误差（±0.5 容差）。
失败 = 回归，必须修代码而非改 golden。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.embodiment_drafts import score_embodiment_table, table_to_ingredients

GOLDEN = Path(__file__).parent / "golden" / "embodiment_tables.json"
_TOL = 0.5


def _cases():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return data["cases"], data["_meta"].get("tolerance_wt_pct", _TOL)


def test_golden_ingredient_recall():
    cases, _ = _cases()
    misses: list[str] = []
    for c in cases:
        table = {
            "headers": c["table"]["headers"],
            "rows": c["table"]["rows"],
            "label": c["label"],
            "page_hint": None,
        }
        emb = table_to_ingredients(table)
        got = {i.get("name") for i in (emb.get("ingredients") if emb else [])}
        for want in c["expected"]["ingredients"]:
            if want not in got:
                misses.append(f"{c['id']}: 缺组分 {want} (got={sorted(got)})")
    assert not misses, "组分名检出回归:\n" + "\n".join(misses)


def test_golden_wt_pct_accuracy():
    cases, tol = _cases()
    bad: list[str] = []
    for c in cases:
        table = {
            "headers": c["table"]["headers"],
            "rows": c["table"]["rows"],
            "label": c["label"],
            "page_hint": None,
        }
        emb = table_to_ingredients(table)
        got = [i.get("weight_pct") for i in (emb.get("ingredients") if emb else [])]
        want = c["expected"]["wt_pct"]
        if len(got) != len(want):
            bad.append(f"{c['id']}: 个数 {len(got)} != {len(want)}")
            continue
        for g, w in zip(got, want):
            if g is None or abs(g - w) > tol:
                bad.append(f"{c['id']}: wt% {g} vs 预期 {w}（容差 {tol}）")
    assert not bad, "wt% 归一化回归:\n" + "\n".join(bad)


def test_golden_tables_score_positive():
    """所有 golden 表都应被评为可用（score >= 0），否则示例组漏检。"""
    cases, _ = _cases()
    bad: list[str] = []
    for c in cases:
        table = {
            "headers": c["table"]["headers"],
            "rows": c["table"]["rows"],
            "label": c["label"],
            "page_hint": None,
        }
        emb = table_to_ingredients(table)
        if not emb:
            bad.append(f"{c['id']}: table_to_ingredients 返回 None")
            continue
        if score_embodiment_table(table, emb) < 0:
            bad.append(f"{c['id']}: score < 0（被判为不可用）")
    assert not bad, "示例组漏检:\n" + "\n".join(bad)
