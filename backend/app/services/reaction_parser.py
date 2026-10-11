"""反应式文本 → 结构化反应解析（2026-10-11）。

文本形式的化学反应式（如 "A + B → C"）用 LLM 解析为结构化数据：
reactants / reagents / products / conditions / SMILES。

设计：
- 零新依赖：复用现有 DeepSeek/OpenAI-compatible LLM 基础设施。
- fail-open：LLM 不可用/解析失败时返回 {"parsed": False}，不阻塞。
- 与图像反应式（RxnIM POC）互补：文本走 LLM，图像走视觉。
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# 反应箭头模式：→ ⇒ ⟶ > 等
_REACTION_ARROW_RE = re.compile(r"(→|⇒|⟶|⟹|-->|=>|\s+>\s+)")

# 疑似反应式的粗判：含箭头 + 化学相关词
_CHEM_HINT_RE = re.compile(
    r"(react|yield|product|reagent|catalyst|mol\b|g\b|°C| reflux|stir)",
    re.IGNORECASE,
)


def looks_like_reaction(text: str) -> bool:
    """粗判文本是否像反应式（有箭头 + 化学上下文）。"""
    return bool(text and _REACTION_ARROW_RE.search(text) and _CHEM_HINT_RE.search(text))


def parse_reaction_text(text: str, *, timeout_s: float = 60.0) -> dict:
    """LLM 解析反应式文本。返回结构化 dict，失败时 {"parsed": False}。

    成功返回：
    {
      "parsed": True,
      "reactants": [{"name": str, "smiles": str|None, "amount": str|None}],
      "reagents": [...],
      "products": [{"name": str, "smiles": str|None, "yield": str|None}],
      "conditions": {"temperature": str|None, "solvent": str|None, "time": str|None},
      "reaction_smiles": str|None,  # reactants>>products
    }
    """
    if not looks_like_reaction(text):
        return {"parsed": False, "reason": "not a reaction-like text"}
    try:
        from ..config import get_settings
        from .llm import _complete_openai_compatible

        s = get_settings()
        api_key = s.deepseek_api_key or s.openai_api_key
        if not api_key:
            return {"parsed": False, "reason": "no LLM API key"}
        model = s.deepseek_model or "deepseek-chat"
        prompt = (
            "Parse the following chemical reaction description into JSON.\n"
            "Return ONLY valid JSON with keys: reactants, reagents, products, conditions, reaction_smiles.\n"
            "Each reactant/reagent/product: {\"name\": str, \"smiles\": str or null, \"amount\": str or null}.\n"
            "conditions: {\"temperature\": str or null, \"solvent\": str or null, \"time\": str or null}.\n"
            "reaction_smiles: SMILES reaction format (reactants>>products) or null.\n\n"
            f"Reaction: {text[:2000]}"
        )
        out = _complete_openai_compatible(
            prompt, api_key, model, max_tokens=1000,
            base_url=s.deepseek_base_url,
        )
        if not out:
            return {"parsed": False, "reason": "empty LLM response"}
        # 提取 JSON（容忍 markdown 代码块）
        m = re.search(r"\{.*\}", out, re.DOTALL)
        if not m:
            return {"parsed": False, "reason": "no JSON in response"}
        data = json.loads(m.group(0))
        data["parsed"] = True
        return data
    except Exception as exc:
        logger.debug("parse_reaction_text failed: %s", exc)
        return {"parsed": False, "reason": str(exc)[:100]}
