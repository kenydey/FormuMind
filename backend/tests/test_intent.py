"""Tests for the natural-language intent parser (v0.6, P1)."""
from app.domain.schemas import IntentResult, ProductDomain, Substrate
from app.services.intent import _offline_parse, parse_intent


def test_offline_parse_anticorrosion_chinese():
    r = _offline_parse("开发汽车底盘环保水性环氧防腐涂料，耐盐雾1000小时，120℃固化")
    assert r.engine == "offline-heuristic"
    assert r.requirement.domain == ProductDomain.anticorrosion_coating
    assert r.requirement.salt_spray_hours == 1000.0
    assert r.requirement.cure_temperature_c == 120.0
    assert r.requirement.voc_limit_gpl == 250.0  # 环保/水性 → low VOC
    assert "salt_spray_hours" in r.extracted_fields


def test_offline_parse_degreaser_english():
    r = _offline_parse("Develop an alkaline degreaser for galvanized steel, 95% oil removal at pH 12")
    assert r.requirement.domain == ProductDomain.degreaser
    assert r.requirement.substrate == Substrate.galvanized_steel
    assert r.requirement.cleaning_efficiency == 95.0
    assert r.requirement.ph_target == 12.0


def test_offline_parse_surface_treatment():
    r = _offline_parse("铝合金前处理，无铬钝化转化膜")
    assert r.requirement.domain == ProductDomain.surface_treatment
    assert r.requirement.substrate == Substrate.aluminum


def test_offline_parse_explicit_voc():
    r = _offline_parse("溶剂型防腐涂料，VOC 不超过 420 g/L")
    assert r.requirement.voc_limit_gpl == 420.0
    assert "voc_limit_gpl" in r.extracted_fields


def test_offline_parse_defaults_when_vague():
    r = _offline_parse("做一个涂料")
    # domain detected, but minimal other fields
    assert r.requirement.domain == ProductDomain.anticorrosion_coating
    assert r.requirement.substrate == Substrate.carbon_steel
    assert 0.0 <= r.confidence <= 1.0


def test_parse_intent_returns_valid_result():
    r = parse_intent("耐盐雾800小时的镀锌钢防腐底漆")
    assert isinstance(r, IntentResult)
    assert r.engine in ("llm", "offline-heuristic")
    assert r.requirement.domain in ProductDomain
    assert 0.0 <= r.confidence <= 1.0


def test_confidence_increases_with_more_fields():
    sparse = _offline_parse("做一个脱脂剂")
    rich = _offline_parse("脱脂剂，镀锌钢，清洗效率95%，pH 12，盐雾100小时")
    assert rich.confidence >= sparse.confidence
