"""Unit tests for table_normalize (W3-1 / P1-19).

TDS/性能表归一化：属性名中英映射、单位同量纲符号归一、数值解析、
未映射 warning、kind 过滤、fail-open、sidecar 持久化、parsing 接线。
"""
from __future__ import annotations

import hashlib
import json

from app.services import parsing, table_contract as tc
from app.services import table_normalize as tn
from app.services.table_contract import TableAsset


def _asset(kind="tds_sds", headers=None, rows=None, caption="表1 TDS"):
    return TableAsset(
        table_id="s#p01-00",
        source_id="s",
        page_no=1,
        caption=caption,
        kind=kind,
        headers=headers if headers is not None else ["项目", "典型值"],
        rows=rows if rows is not None else [],
    )


def _prop(ps, name):
    return next(p for p in ps.properties if p.name == name)


# ── property-name mapping ───────────────────────────────────────────────────

def test_solids_content_cn():
    ps = tn.normalize_table(_asset(rows=[["固含量", "65%"]]))
    p = _prop(ps, "固含量")
    assert p.name_normalized == "solids_content"
    assert p.value == 65.0
    assert p.unit == "%" and p.unit_normalized == "%"
    assert p.raw_text == "65%"


def test_viscosity_unit_symbol_normalized():
    ps = tn.normalize_table(_asset(rows=[["黏度", "2500 mPa·s"]]))
    p = _prop(ps, "黏度")
    assert p.name_normalized == "viscosity"
    assert p.value == 2500.0
    assert p.unit_normalized == "mPa.s"


def test_english_property_case_insensitive_and_cp():
    ps = tn.normalize_table(_asset(rows=[["Viscosity", "120 cP"]]))
    p = _prop(ps, "Viscosity")
    assert p.name_normalized == "viscosity"
    assert p.value == 120.0
    assert p.unit_normalized == "mPa.s"  # 1:1, same dimension


def test_ph_density_fullwidth_percent():
    ps = tn.normalize_table(_asset(rows=[
        ["pH值", "8.5"],
        ["密度", "1.25 g/cm³"],
        ["固含量", "65％"],
    ]))
    assert _prop(ps, "pH值").name_normalized == "ph"
    assert _prop(ps, "pH值").value == 8.5
    d = _prop(ps, "密度")
    assert d.name_normalized == "density"
    assert d.value == 1.25 and d.unit_normalized == "g/cm3"
    assert _prop(ps, "固含量").unit_normalized == "%"


def test_salt_spray_gloss_paren_stripped():
    ps = tn.normalize_table(_asset(rows=[
        ["耐盐雾性", "1000 h"],
        ["光泽度（60°）", "85 GU"],
    ]))
    s = _prop(ps, "耐盐雾性")
    assert s.name_normalized == "salt_spray" and s.value == 1000.0
    assert s.unit_normalized == "h"
    g = _prop(ps, "光泽度（60°）")
    assert g.name_normalized == "gloss" and g.value == 85.0
    assert g.unit_normalized == "GU"


def test_adhesion_grade_zero():
    ps = tn.normalize_table(_asset(rows=[["附着力", "0级"]]))
    p = _prop(ps, "附着力")
    assert p.name_normalized == "adhesion"
    assert p.value == 0.0 and p.unit_normalized == "级"


def test_unmapped_property_warns_not_guessed():
    ps = tn.normalize_table(_asset(rows=[["神秘指标", "42"]]))
    p = _prop(ps, "神秘指标")
    assert p.name_normalized == ""
    assert p.value == 42.0  # numeric value still parsed
    assert any("unmapped_property" in w and "神秘指标" in w for w in ps.warnings)


# ── value parsing ───────────────────────────────────────────────────────────

def test_non_numeric_keeps_raw():
    ps = tn.normalize_table(_asset(rows=[["外观", "平整光滑"]]))
    p = _prop(ps, "外观")
    assert p.name_normalized == "appearance"
    assert p.value is None
    assert p.raw_text == "平整光滑"


def test_range_is_ambiguous_no_value():
    ps = tn.normalize_table(_asset(rows=[["表干时间", "2~4 h"]]))
    p = _prop(ps, "表干时间")
    assert p.name_normalized == "drying_time_surface"
    assert p.value is None
    assert p.raw_text == "2~4 h"
    assert any("ambiguous_value" in w for w in ps.warnings)


def test_hardness_grade_not_misparsed_as_hours():
    # "2H" is a pencil-hardness grade — must not become 2.0 hours.
    ps = tn.normalize_table(_asset(rows=[["铅笔硬度", "2H"]]))
    p = _prop(ps, "铅笔硬度")
    assert p.name_normalized == "hardness"
    assert p.value is None
    assert p.raw_text == "2H"


def test_separate_unit_column():
    ps = tn.normalize_table(_asset(
        headers=["项目", "指标", "单位"],
        rows=[["黏度", "2500", "mPa·s"]],
    ))
    p = _prop(ps, "黏度")
    assert p.value == 2500.0 and p.unit_normalized == "mPa.s"


def test_recipe_kind_processed():
    ps = tn.normalize_table(_asset(
        kind="recipe",
        headers=["组分", "配比"],
        rows=[["环氧树脂", "100"]],
        caption="表1 配方",
    ))
    assert len(ps.properties) == 1
    assert _prop(ps, "环氧树脂").value == 100.0


# ── kind filtering & fail-open ───────────────────────────────────────────────

def test_kind_other_skipped():
    ps = tn.normalize_table(_asset(kind="other", rows=[["A", "1"]]))
    assert ps.properties == []
    assert any("skipped" in w for w in ps.warnings)


def test_never_raises_on_garbage():
    bad = TableAsset(table_id="", source_id="", page_no=1, caption="",
                     kind="tds_sds", headers=[], rows=[["a"]])
    ps = tn.normalize_table(bad)  # must not raise
    assert isinstance(ps, tn.PropertySet)
    ragged = _asset(rows=[["只有一列"]])
    ps2 = tn.normalize_table(ragged)
    assert isinstance(ps2, tn.PropertySet)


def test_normalize_tables_fail_open_per_asset():
    good = _asset(rows=[["固含量", "65%"]])
    ps_list = tn.normalize_tables([good, None, good])
    assert len(ps_list) == 3
    assert ps_list[0].properties and ps_list[2].properties


def test_propertyset_dict_roundtrip():
    ps = tn.normalize_table(_asset(rows=[["固含量", "65%"]]))
    d = ps.to_dict()
    ps2 = tn.PropertySet.from_dict(d)
    assert ps2.table_id == ps.table_id
    assert ps2.properties[0].name_normalized == "solids_content"
    assert ps2.properties[0].value == 65.0


# ── sidecar persistence ─────────────────────────────────────────────────────

def test_sidecar_roundtrip_with_property_sets(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    asset = _asset(rows=[["固含量", "65%"]])
    ps = tn.normalize_table(asset)
    path = tc.save_tables("s", [asset], property_sets=[ps.to_dict()])
    assert path is not None and path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["property_sets"][0]["properties"][0]["name_normalized"] == "solids_content"
    loaded = tc.load_property_sets("s")
    assert len(loaded) == 1
    assert loaded[0]["properties"][0]["value"] == 65.0
    # tables still load as before
    assert len(tc.load_tables("s")) == 1


def test_sidecar_without_property_sets_loads_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    tc.save_tables("s", [_asset(rows=[["固含量", "65%"]])])
    assert tc.load_property_sets("s") == []
    assert tc.load_property_sets("missing") == []


# ── parsing.py wiring ───────────────────────────────────────────────────────

TDS_MD = """表 2 TDS 技术数据
| 项目 | 典型值 |
|---|---|
| 固含量 | 65% |
| 黏度 | 2500 mPa·s |
"""

def test_wiring_persists_property_sets(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path))
    result = parsing.ParseResult(TDS_MD, "docling")
    out = parsing._maybe_extract_tables(result, b"tds-bytes")
    assert len(out.tables) == 1
    assert out.tables[0].kind == "tds_sds"
    # The sidecar is persisted by persist_table_sidecar, keyed by the real
    # source UUID (the key every reader uses), not by the file-bytes sha256.
    key = "11111111-2222-3333-4444-555555555555"
    parsing.persist_table_sidecar(key, out.tables)
    sets = tc.load_property_sets(key)
    assert len(sets) == 1
    names = {p["name_normalized"] for p in sets[0]["properties"]}
    assert {"solids_content", "viscosity"} <= names
