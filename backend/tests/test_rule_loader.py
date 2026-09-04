"""R1: 领域规则配置中心测试(2026-09-04)。"""
from __future__ import annotations

import pytest

from app.services import rule_loader
from app.services.rule_loader import load_rules, reload_rules


@pytest.fixture(autouse=True)
def _clean_rules_cache():
    yield
    reload_rules()


def _rules_default(kind: str) -> dict:
    return rule_loader._FALLBACKS[kind]


# ── 默认配置 == 迁移前硬编码(锚定, 防漂移) ────────────────────────────────


def test_default_acid_stability_matches_pre_migration_values():
    """默认 TOML 加载结果与迁移前 acid_stability.py 硬编码逐键一致。"""
    rules = load_rules("acid_stability")
    fb = _rules_default("acid_stability")
    assert rules == fb
    assert rules["strong_alkali"]["exact"] == [
        "Sodium metasilicate", "Sodium tripolyphosphate",
    ]
    assert "Sodium hydroxide" in rules["strong_alkali"]["prefixes"]
    assert "Zinc dust" in rules["reactive_metals"]["names"]
    assert "carbonate" in rules["carbonate_fillers"]["substrings"]


def test_default_linker_roles_matches_hints():
    rules = load_rules("linker_roles")["role_hints"]
    assert rules == _rules_default("linker_roles")["role_hints"]
    # 迁移前 _ROLE_HINTS 语义抽查(flat hint→role 等价于嵌套)
    assert "corrosion_inhibitor" in rules["inhibitor"]
    assert "epoxy" in rules["resin"]


def test_default_ambiguous_terms_structure():
    rules = load_rules("ambiguous_terms")
    assert set(rules) == {"水性", "快干", "环氧"}
    assert rules["环氧"]["candidates"][0] == [
        "双酚A型环氧树脂", "chem:catalog:bisphenol_a_epoxy",
    ]


# ── 外部覆盖(FORMUMIND_RULES_DIR) ────────────────────────────────────────


def test_override_dir_adds_new_alkali(tmp_path, monkeypatch):
    """运维在覆盖目录加新碱(四甲基氢氧化铵)→ acid_stability 命中。"""
    over = tmp_path / "rules"
    over.mkdir()
    (over / "acid_stability.toml").write_text(
        'strong_alkali = { exact = ["Sodium metasilicate", "Tetramethylammonium hydroxide"], '
        'prefixes = [], reason = "强碱 {names} 与酸性浴 pH 冲突（中和放热，浴失控）" }\n'
        "carbonate_fillers = { substrings = [], reason = \"x {names}\" }\n"
        "reactive_metals = { names = [], reason = \"x {names}\" }\n"
        "amine_neutralised = { substrings = [], reason = \"x {names}\" }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FORMUMIND_RULES_DIR", str(over))
    reload_rules()
    from app.domain.schemas import Formulation, Ingredient
    from app.services.acid_stability import check_acid_stability

    form = Formulation(
        name="t", domain="autodeposition_coating", rationale="t",
        ingredients=[
            Ingredient(name="Tetramethylammonium hydroxide", role="additive", weight_pct=2.0),
            Ingredient(name="Deionized water", role="solvent", weight_pct=98.0),
        ],
    )
    res = check_acid_stability(form)
    assert not res.stable
    assert any("四甲基" in r or "Tetramethylammonium" in r for r in res.reasons)


def test_override_dir_new_role_hint(tmp_path, monkeypatch):
    """覆盖目录加 rheology_modifier → additive → _infer_role 返回 additive。"""
    over = tmp_path / "rules"
    over.mkdir()
    (over / "linker_roles.toml").write_text(
        "role_hints = { additive = [\"rheology\", \"additive\"], "
        "resin = [\"resin\"] }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FORMUMIND_RULES_DIR", str(over))
    reload_rules()
    from app.services.kg.formulation_linker import _infer_role

    assert _infer_role("Rheology Modifier 9000") == "additive"


def test_override_dir_extends_ambiguity_lexicon(tmp_path, monkeypatch):
    over = tmp_path / "rules"
    over.mkdir()
    (over / "ambiguous_terms.toml").write_text(
        '["流平"]\ncandidates = [["有机硅流平剂", ""], ["丙烯酸流平剂", ""]]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FORMUMIND_RULES_DIR", str(over))
    reload_rules()
    from app.domain.chat_schemas import ChatTurn, ClarifiedEntity
    from app.services.chat_clarify import detect_clarification
    from app.config import Settings

    class _S:
        chat_clarification_enabled = True
        kg_enabled = False  # 跳过 KG 分支, 直达 lexicon(词典扩展验证)

    opt = detect_clarification("用流平的方案", [], None, settings=_S())
    assert opt is not None
    assert opt.ambiguous_term == "流平"
    assert "有机硅流平剂" in opt.possible_meanings


# ── 兜底: 文件缺失/坏文件 → 内置默认, 不破坏功能 ─────────────────────────


def test_missing_rules_dir_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_RULES_DIR", str(tmp_path / "nope"))
    reload_rules()
    assert load_rules("acid_stability") == _rules_default("acid_stability")
    assert load_rules("linker_roles") == _rules_default("linker_roles")


def test_bad_toml_falls_back(tmp_path, monkeypatch):
    over = tmp_path / "rules"
    over.mkdir()
    (over / "acid_stability.toml").write_text("not [ valid toml ===")
    monkeypatch.setenv("FORMUMIND_RULES_DIR", str(over))
    reload_rules()
    assert load_rules("acid_stability") == _rules_default("acid_stability")


def test_unknown_kind_raises():
    with pytest.raises(KeyError):
        load_rules("no_such_table")


def test_consumers_work_with_default_rules():
    """默认规则下三个消费方行为与迁移前一致(回归锚)。"""
    from app.domain.schemas import Formulation, Ingredient
    from app.services.acid_stability import check_acid_stability

    form = Formulation(
        name="t", domain="autodeposition_coating", rationale="t",
        ingredients=[
            Ingredient(name="Zinc dust", role="pigment", weight_pct=5.0),
            Ingredient(name="Sodium hydroxide", role="additive", weight_pct=1.0),
            Ingredient(name="Deionized water", role="solvent", weight_pct=94.0),
        ],
    )
    res = check_acid_stability(form)
    assert not res.stable
    assert any("析氢" in r for r in res.reasons)
    assert any("强碱" in r for r in res.reasons)

    from app.services.kg.formulation_linker import _infer_role
    assert _infer_role("Epoxy resin E51") == "resin"
    assert _infer_role("FoamStar defoamer") == "additive"
    assert _infer_role("Something exotic new") == "unknown"
