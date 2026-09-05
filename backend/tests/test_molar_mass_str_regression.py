"""molar_mass str 逃逸回归测试(2026-09-05 recommend 503 修复)."""
import pytest

from app.domain.chemistry import _parse_molar_mass, validate_formulation
from app.domain.schemas import Formulation, Ingredient


def _form(ingredients: list[Ingredient]) -> Formulation:
    return Formulation(
        name="t",
        domain="surface_treatment",
        ingredients=ingredients,
        summary="",
        predicted={},
    )


def _ing(
    name: str = "x",
    role: str = "resin",
    formula: str | None = None,
    molar_mass: float | None = None,
) -> Ingredient:
    return Ingredient(
        name=name, role=role, formula=formula, molar_mass=molar_mass, weight_pct=100.0
    )


def test_parse_molar_mass_accepts_numbers_and_messy_strings():
    assert _parse_molar_mass(381.9) == 381.9
    assert _parse_molar_mass("381.9") == 381.9
    assert _parse_molar_mass("~382 g/mol") == 382.0
    assert _parse_molar_mass("约 381.9 g/mol") == 381.9
    assert _parse_molar_mass("1,200") == 1200.0
    assert _parse_molar_mass(None) is None
    assert _parse_molar_mass("not-a-number") is None
    assert _parse_molar_mass(True) is None


def test_ingredient_schema_coerces_unit_bearing_molar_mass():
    """B13: Ingredient validators must accept '381.9 g/mol', not drop to None."""
    ing = Ingredient(
        name="epoxy",
        role="resin",
        weight_pct=50.0,
        molar_mass="381.9 g/mol",
        equivalents="~1.5 eq",
        mmol="约 12.0 mmol",
    )
    assert ing.molar_mass == pytest.approx(381.9)
    assert ing.equivalents == pytest.approx(1.5)
    assert ing.mmol == pytest.approx(12.0)


def test_validate_survives_str_molar_mass_and_normalises():
    # 复现 503: model_copy(update) 让 '381.9' str 逃逸进 Ingredient
    ing = _ing(name="Waterborne epoxy ester emulsion", formula="C20H28ClNO4")
    setattr(ing, "molar_mass", "381.9")  # pydantic 赋值无校验 —— 同 model_copy 逃逸路径
    form = _form([ing])
    warnings = validate_formulation(form)  # 修复前此处 TypeError
    assert isinstance(ing.molar_mass, float)
    assert ing.molar_mass == pytest.approx(381.9, abs=0.01)


def test_validate_unparseable_mass_falls_back_to_computed():
    ing = _ing(name="epoxy", formula="C20H28ClNO4")
    setattr(ing, "molar_mass", "N/A")  # 同 model_copy 逃逸路径
    form = _form([ing])
    warnings = validate_formulation(form)
    assert isinstance(ing.molar_mass, float)  # 回填 formula 计算值
    assert ing.molar_mass == pytest.approx(381.9, abs=0.3)


def test_validate_still_flags_genuine_mismatch():
    ing = _ing(name="epoxy", formula="C20H28ClNO4", molar_mass=200.0)
    form = _form([ing])
    warnings = validate_formulation(form)
    assert any("declared M=200.0" in w for w in warnings)
