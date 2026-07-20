"""Trade-product disclaimer for grounded Q&A."""
from __future__ import annotations

TRADE_PRODUCT_DISCLAIMER = (
    "部分引用涉及商业牌号或混合物，原文可能未披露具体化学组成。"
    "不得推断 CAS 或分子结构；仅描述原文表述及应用场景。"
)


def evidence_has_unresolved_trade(evidence_list) -> bool:
    for ev in evidence_list:
        for ref in getattr(ev, "entity_refs", None) or []:
            status = ref.composition_status if hasattr(ref, "composition_status") else ref.get("composition_status")
            if status in ("unknown", "proprietary", "mixture"):
                return True
    return False
