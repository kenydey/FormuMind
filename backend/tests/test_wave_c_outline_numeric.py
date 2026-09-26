"""Wave C: pack-driven outline + numeric fidelity warnings."""
from __future__ import annotations

from app.services.wiki.storm_outline import build_deterministic_outline
from app.services.wiki.storm_polish import collect_numeric_fidelity_warnings


def test_outline_drops_doe_when_pack_empty():
    pack = {
        "title": "环氧底漆",
        "requirements": {"rows": [{"metric": "salt_spray_hours", "value": 720}]},
        "formula": {"rows": [{"name": "E-51", "role": "resin", "weight_pct": 40}]},
        "literature": {"rows": [{"source_id": "s1", "title": "P"}]},
    }
    outline = build_deterministic_outline(pack, project_id="p1", topic="环氧")
    ids = {s.section_id for s in outline.sections}
    assert "sec_background" in ids
    assert "sec_doe_lab" not in ids
    # queries should be differentiated
    lit = next(s for s in outline.sections if s.section_id == "sec_literature")
    assert any("机理" in q or "专利" in q for q in lit.retrieval_queries)


def test_outline_keeps_doe_when_rows_present():
    pack = {
        "title": "t",
        "doe": {"rows": [{"factor": "pH"}]},
        "requirements": {"rows": [{"metric": "salt_spray_hours", "value": 500}]},
        "formula": {"rows": [{"name": "resin"}]},
    }
    outline = build_deterministic_outline(pack, project_id="p1")
    ids = {s.section_id for s in outline.sections}
    assert "sec_doe_lab" in ids


def test_numeric_fidelity_flags_near_miss():
    pack = {
        "requirements": {"rows": [{"metric": "salt_spray_hours", "value": 720}]},
        "formula": {"rows": []},
    }
    md = "# R\n\n盐雾耐受约 680 小时，接近目标。\n"
    warns = collect_numeric_fidelity_warnings(md, pack)
    assert warns
    assert any("数值存疑" in w for w in warns)
