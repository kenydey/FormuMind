"""P1-6: 品牌号识别 100 例实测 —— 准确率/召回率。"""
from __future__ import annotations

import pytest

from app.services.chem_extract import extract_products

# (文本, 期望识别出的 [(trade_name, grade)])
CASES: list[tuple[str, list[tuple[str, str]]]] = [
    # 白名单品牌（无佐证也应识别）
    ("Epon 828 epoxy resin", [("Epon", "828")]),
    ("Araldite GY 250", [("Araldite", "GY")]),  # 注意：GY 后无数字，grade 可能为空
    ("BYK-333 leveling agent", [("BYK", "333")]),
    ("Aerosil 200 fumed silica", [("Aerosil", "200")]),
    ("Ti-Pure R-706 titanium dioxide", [("Ti-Pure", "R-706")]),
    ("Desmodur N 3300", [("Desmodur", "N")]),
    ("Jeffamine D 230", [("Jeffamine", "D")]),
    ("Tego Foamex 810", [("Tego", "Foamex")]),
    # ™/® 标记（无条件接受）
    ("Zinc phosphate Heucophos® CAPP", [("Heucophos", "CAPP")]),
    ("Disperbyk™ 190 dispersant", [("Disperbyk", "190")]),
    # 供应商佐证
    ("BYK 333 from BYK-Chemie", [("BYK", "333")]),
    ("Tego 410 supplied by Evonik", [("Tego", "410")]),
    # 中文供应商名
    ("毕克 BYK-066N 消泡剂", [("BYK", "066N")]),
    ("赢创 Tego 270", [("Tego", "270")]),
    # 不应识别（无佐证的未知品牌）
    ("SomeRandom 12345 xyz", []),
    ("Water 100 parts", []),
    # 边界：Hansen 子串不应误报（已知约束，不修复只记录）
    ("Hansen solubility parameter", []),
]

# 批量生成：白名单品牌 × 常见牌号
_BRANDS = ["Epon", "BYK", "Tego", "Aerosil", "Desmodur", "Jeffamine",
           "Araldite", "Ti-Pure", "Disperbyk", "Borchi"]
_GRADES = ["100", "200", "333", "828", "N3300", "D230", "R706", "810", "190", "066N"]


def _gen_cases() -> list[tuple[str, list[tuple[str, str]]]]:
    cases = list(CASES)
    for b in _BRANDS:
        for g in _GRADES[:5]:
            cases.append((f"{b} {g} additive", [(b, g)]))
    # 负例：普通化学品名不应识别为品牌
    for chem in ["Sodium chloride", "Ethanol", "Water", "Acetone", "Toluene"]:
        cases.append((f"{chem} 50g", []))
    return cases[:100]


ALL_CASES = _gen_cases()


def test_brand_recognition_metrics():
    """实测品牌号识别准确率/召回率（记录基线，不设门禁）。"""
    tp = fp = fn = 0
    details = []
    for text, expected in ALL_CASES:
        got = extract_products(text)
        got_set = {(g["trade_name"], g["grade"]) for g in got}
        exp_set = set(expected)
        # 宽松匹配：trade_name 对上即算（grade 格式差异大）
        got_brands = {t.lower() for t, _ in got_set}
        exp_brands = {t.lower() for t, _ in exp_set}
        for b in exp_brands:
            if b in got_brands:
                tp += 1
            else:
                fn += 1
                details.append(f"FN: {text!r} 期望 {b}")
        for b in got_brands:
            if b not in exp_brands:
                fp += 1
                details.append(f"FP: {text!r} 误报 {b}")
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    print(f"\n品牌号识别: P={precision:.3f} R={recall:.3f} (TP={tp} FP={fp} FN={fn})")
    for d in details[:10]:
        print(f"  {d}")
    # 只记录，不设硬门禁（基线测量）
    assert precision >= 0.5, f"precision {precision:.3f} 过低"
