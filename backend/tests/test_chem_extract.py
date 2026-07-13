"""Chunk-level chemistry & product entity extraction (KB stream P2, rule tier)."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import chem_extract as cx


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── CAS ──────────────────────────────────────────────────────────────────────


def test_cas_checksum_accepts_valid_rejects_invalid():
    text = "磷酸锌 CAS 7779-90-0，环氧树脂 25068-38-6，伪号 1234-56-7 应被拒绝。"
    found = cx.extract_cas(text)
    assert "7779-90-0" in found
    assert "25068-38-6" in found
    assert "1234-56-7" not in found  # checksum fails


def test_cas_dedupes():
    assert cx.extract_cas("50-78-2 与 50-78-2") == ["50-78-2"]


# ── molecular formulas ───────────────────────────────────────────────────────


def test_formulas_validated_by_parser():
    text = "以 Zn3(PO4)2 与 H2SO4 为原料，产物含 Al2(SO4)3。"
    found = cx.extract_formulas(text)
    assert "Zn3(PO4)2" in found
    assert "H2SO4" in found
    assert "Al2(SO4)3" in found


def test_formula_acronym_traps_rejected():
    text = "NO evidence IN the US: PH testing AS described."
    assert cx.extract_formulas(text) == []


def test_two_letter_element_combo_accepted():
    found = cx.extract_formulas("NaCl 溶液与 ZnO 颜料。")
    assert "NaCl" in found
    assert "ZnO" in found


# ── reactions ────────────────────────────────────────────────────────────────


def test_reaction_equation_extracted():
    text = "固化反应：2Al + 3H2SO4 → Al2(SO4)3 + 3H2 放热。"
    rxns = cx.extract_reactions(text)
    assert len(rxns) == 1
    assert "H2SO4" in rxns[0]["reactants"]
    assert "Al2(SO4)3" in rxns[0]["products"]


def test_prose_arrow_without_formulas_ignored():
    assert cx.extract_reactions("从需求 → 方案 → 落地的流程。") == []


def test_latex_arrow_supported():
    text = "反应式 Zn3(PO4)2 + H2O \\rightarrow Zn(OH)2 沉积。"
    rxns = cx.extract_reactions(text)
    assert rxns and "Zn3(PO4)2" in rxns[0]["reactants"]


# ── SMILES ───────────────────────────────────────────────────────────────────


def test_smiles_requires_rdkit():
    found = cx.extract_smiles("环氧丙烷 C1CO1 与苯酚 c1ccccc1O。")
    try:
        import rdkit  # noqa: F401

        assert any(s["raw"] == "C1CO1" for s in found)
    except ImportError:
        assert found == []  # no RDKit → no SMILES claims (too noisy)


# ── commercial products ──────────────────────────────────────────────────────


def test_trademark_symbol_accepted_unconditionally():
    found = cx.extract_products("采用 Aerosil® 200 气相二氧化硅调节触变性。")
    assert any(p["trade_name"] == "Aerosil" and p["grade"] == "200" for p in found)


def test_known_brand_grade_accepted():
    found = cx.extract_products("环氧树脂选用 Epon 828（双酚A型）。")
    assert any(p["trade_name"] == "Epon" and p["grade"] == "828" for p in found)


def test_unknown_brand_needs_supplier_context():
    assert cx.extract_products("参见 Zorbex 450 的描述。") == []
    found = cx.extract_products("Zorbex 450, available from Acme Chemical, was used.")
    assert any(p["trade_name"] == "Zorbex" for p in found)


def test_supplier_name_nearby_is_captured():
    found = cx.extract_products("流平剂 BYK-333（毕克化学）。")
    hit = next(p for p in found if p["trade_name"] == "BYK")
    assert hit["grade"] == "333"
    assert "BYK" in hit["supplier"] or "毕克" in hit["supplier"]


def test_figure_table_references_rejected():
    text = "如 Table 3 与 Figure 12 所示，Example 5 的配方见 Claim 2。"
    assert cx.extract_products(text) == []


def test_chinese_supplier_context():
    found = cx.extract_products("固化剂 Ancamine 2500 购自赢创。")
    hit = next(p for p in found if p["trade_name"] == "Ancamine")
    assert hit["grade"] == "2500"


# ── aggregate ────────────────────────────────────────────────────────────────


def test_extract_entities_compact_meta():
    text = (
        "实施例1：Epon 828 一百份，磷酸锌（CAS 7779-90-0，Zn3(PO4)2）十五份。"
        "固化：2Al + 3H2SO4 → Al2(SO4)3 + 3H2。"
    )
    meta = cx.extract_entities(text)
    assert meta is not None
    types = {e["type"] for e in meta["chem"]}
    assert {"cas", "formula", "reaction"} <= types
    assert any(p["trade_name"] == "Epon" for p in meta["products"])


def test_extract_entities_empty_returns_none():
    assert cx.extract_entities("这段文字没有任何化学实体。") is None
