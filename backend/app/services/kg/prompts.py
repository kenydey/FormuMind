"""KG prompts — trade disclaimers and multi-modal extraction."""
from __future__ import annotations

TRADE_PRODUCT_DISCLAIMER = (
    "部分引用涉及商业牌号或混合物，原文可能未披露具体化学组成。"
    "不得推断 CAS 或分子结构；仅描述原文表述及应用场景。"
)

MULTIMODAL_TABLE_EXTRACTION_PROMPT = """你是化学与表面处理领域的专家。请阅读专利/文献中的对比表格图片，并结合提供的上下文文本，
提取表格中的配方实施例、成分浓度、基材与性能指标（如盐雾 NSS、附着力等）。

只输出一个 JSON 对象（不要 markdown 围栏），结构必须严格如下：
{
  "table_type": "comparison" | "single" | "unknown",
  "formulations": [
    {
      "example_id": "实施例编号或名称",
      "substrate": {"name": "基材名称", "pre_treatment": "前处理/涂装体系（可空）"},
      "ingredients": [
        {"name": "成分名称", "concentration": 8.0, "unit": "wt%", "role": "防锈颜料"}
      ],
      "performances": [
        {"metric": "NSS", "value": 720, "unit": "h", "conditions": "5% NaCl 35°C"}
      ]
    }
  ],
  "confidence": 0.0-1.0,
  "notes": "无法辨认的单元格或不确定项说明"
}

规则：
- 数值、单位、化学名称必须忠实于表格，禁止捏造。
- concentration 为数字时勿带单位字符串；unit 单独填写。
- 每个实施例一行/一列对应一个 formulations 元素。
- 表格中未出现的成分或性能不要推断。
- 不确定时降低 confidence 并在 notes 说明。"""


def build_multimodal_table_prompt(context_text: str = "") -> str:
    ctx = (context_text or "").strip()
    if not ctx:
        return MULTIMODAL_TABLE_EXTRACTION_PROMPT
    trimmed = ctx[:4000]
    return f"{MULTIMODAL_TABLE_EXTRACTION_PROMPT}\n\n上下文文本（辅助理解表格）：\n{trimmed}"


def evidence_has_unresolved_trade(evidence_list) -> bool:
    for ev in evidence_list:
        for ref in getattr(ev, "entity_refs", None) or []:
            status = ref.composition_status if hasattr(ref, "composition_status") else ref.get("composition_status")
            if status in ("unknown", "proprietary", "mixture"):
                return True
    return False
