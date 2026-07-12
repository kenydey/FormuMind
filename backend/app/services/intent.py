"""Natural-language R&D intent parser (v0.6, P1).

Turns a free-text project description (Chinese or English) into a structured
``Requirement``. Uses the configured LLM for structured extraction when a key
is present; otherwise falls back to a pure-regex/keyword heuristic so the
feature works fully offline.
"""
from __future__ import annotations

import logging
from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import re

from ..domain.project_spec import default_objectives_for, normalize_requirement
from ..domain.schemas import (
    IntentResult,
    ProductDomain,
    Requirement,
    Substrate,
)

logger = logging.getLogger(__name__)

# ── Keyword tables for the offline heuristic ─────────────────────────────────

_DOMAIN_KEYWORDS: list[tuple[ProductDomain, tuple[str, ...]]] = [
    (ProductDomain.degreaser, ("脱脂", "清洗", "除油", "degreas", "cleaning", "cleaner")),
    (ProductDomain.surface_treatment,
     ("磷化", "钝化", "转化", "前处理", "phosphat", "passivat", "conversion", "pretreat")),
    (ProductDomain.anticorrosion_coating,
     ("防腐", "涂料", "底漆", "面漆", "coating", "anticorros", "anti-corros", "primer", "paint")),
]

_SUBSTRATE_KEYWORDS: list[tuple[Substrate, tuple[str, ...]]] = [
    (Substrate.galvanized_steel, ("镀锌", "galvaniz")),
    (Substrate.stainless_steel, ("不锈钢", "stainless")),
    (Substrate.aluminum, ("铝", "alumin")),
    (Substrate.magnesium_alloy, ("镁", "magnesium")),
    (Substrate.carbon_steel, ("碳钢", "冷轧", "carbon steel", "mild steel", "cold-rolled")),
]


def _detect_domain(text: str) -> ProductDomain | None:
    low = text.lower()
    for domain, keys in _DOMAIN_KEYWORDS:
        if any(k.lower() in low for k in keys):
            return domain
    return None


def _detect_substrate(text: str) -> Substrate | None:
    low = text.lower()
    for sub, keys in _SUBSTRATE_KEYWORDS:
        if any(k.lower() in low for k in keys):
            return sub
    return None


def _first_number_near(text: str, anchors: tuple[str, ...], pattern: str) -> float | None:
    """Find the first numeric match of *pattern* anywhere; bias toward anchors.

    Searches a window around each anchor keyword first, then the whole text.
    """
    low = text.lower()
    rx = re.compile(pattern, re.IGNORECASE)
    for anchor in anchors:
        idx = low.find(anchor.lower())
        if idx != -1:
            window = text[max(0, idx - 25): idx + 40]
            m = rx.search(window)
            if m:
                return float(m.group(1))
    m = rx.search(text)
    return float(m.group(1)) if m else None


def _offline_parse(text: str) -> IntentResult:
    """Regex/keyword heuristic parser — no external dependencies."""
    low = text.lower()
    extracted: list[str] = []

    domain = _detect_domain(text) or ProductDomain.anticorrosion_coating
    if _detect_domain(text) is not None:
        extracted.append("domain")

    substrate = _detect_substrate(text)
    if substrate is not None:
        extracted.append("substrate")
    else:
        substrate = Substrate.carbon_steel

    req = Requirement(domain=domain, substrate=substrate)

    # Salt spray hours: number near 盐雾/salt spray, with h/小时 unit.
    salt = _first_number_near(
        text, ("盐雾", "salt spray", "salt-spray"),
        r"(\d{2,5})\s*(?:小时|h|hr|hours?)\b",
    )
    if salt is not None:
        req.salt_spray_hours = salt
        extracted.append("salt_spray_hours")

    # Cure temperature: number near 固化/烘烤/cure with ℃/°C/C.
    cure = _first_number_near(
        text, ("固化", "烘烤", "cure", "bake"),
        r"(\d{2,3})\s*(?:℃|°c|度|c\b)",
    )
    if cure is not None:
        req.cure_temperature_c = cure
        extracted.append("cure_temperature_c")

    # VOC: explicit number, else eco/waterborne keyword → 250 g/L.
    voc = _first_number_near(
        text, ("voc",), r"voc[^0-9]{0,12}(\d{2,4})",
    )
    if voc is not None:
        req.voc_limit_gpl = voc
        extracted.append("voc_limit_gpl")
    elif any(k in low for k in ("环保", "低voc", "水性", "waterborne", "eco", "low voc", "low-voc")):
        req.voc_limit_gpl = 250.0
        extracted.append("voc_limit_gpl")

    # Cleaning efficiency (degreaser): number near 清洗/除油 with %.
    clean = _first_number_near(
        text, ("清洗", "除油", "脱脂", "cleaning", "removal"),
        r"(\d{2,3})\s*%",
    )
    if clean is not None and domain == ProductDomain.degreaser:
        req.cleaning_efficiency = min(100.0, clean)
        extracted.append("cleaning_efficiency")

    # pH target: number near pH.
    ph = _first_number_near(text, ("ph",), r"ph[^0-9]{0,6}(\d{1,2}(?:\.\d)?)")
    if ph is not None:
        req.ph_target = min(14.0, ph)
        extracted.append("ph_target")

    snippet = text.strip()
    if len(snippet) > 3:
        req.product_type = snippet[:80]
        extracted.append("product_type")
    req.application = substrate.value
    req.objectives = default_objectives_for(req)
    req = normalize_requirement(req)

    # Confidence scales with how many fields we recognised (domain always counts).
    confidence = round(min(0.9, 0.3 + 0.12 * len(set(extracted))), 2)
    return IntentResult(
        requirement=req,
        confidence=confidence,
        extracted_fields=sorted(set(extracted)),
        engine="offline-heuristic",
    )


def _build_intent_prompt(text: str) -> str:
    return f"""You convert a coating / surface-treatment R&D brief into structured fields.

Brief:
{text}

Return ONLY a JSON object with these keys (omit a key if not stated):
{{
  "product_type": "<short product description>",
  "application": "<substrate or use case>",
  "domain": "anticorrosion_coating" | "degreaser" | "surface_treatment",
  "substrate": "carbon_steel" | "galvanized_steel" | "aluminum" | "stainless_steel" | "magnesium_alloy",
  "salt_spray_hours": <number>,
  "film_weight_gsm": <number>,
  "cure_temperature_c": <number>,
  "cleaning_efficiency": <number 0-100>,
  "voc_limit_gpl": <number>,
  "ph_target": <number 0-14>
}}

Rules: if the brief mentions 环保/eco/waterborne and gives no VOC value, set voc_limit_gpl to 250.
Respond with valid JSON only.
"""


def _llm_parse(text: str) -> IntentResult | None:
    """LLM structured extraction; returns None when unavailable or invalid."""
    try:
        from . import llm as llm_service

        data = llm_service.complete_json(_build_intent_prompt(text))
        if not data:
            return None

        # Build a Requirement from the recognised keys; pydantic validates ranges.
        allowed = {
            "product_type", "application", "domain", "substrate", "salt_spray_hours", "film_weight_gsm",
            "cure_temperature_c", "cleaning_efficiency", "voc_limit_gpl", "ph_target",
        }
        fields = {k: v for k, v in data.items() if k in allowed and v is not None}
        if "domain" not in fields:
            return None  # domain is mandatory; let the offline path handle it
        req = Requirement(**fields)
        if not req.objectives:
            req.objectives = default_objectives_for(req)
        req = normalize_requirement(req)
        extracted = sorted(fields.keys())
        return IntentResult(
            requirement=req,
            confidence=round(min(0.95, 0.5 + 0.07 * len(extracted)), 2),
            extracted_fields=extracted,
            engine="llm",
        )
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


def parse_intent(text: str) -> IntentResult:
    """Parse a free-text brief into a structured Requirement.

    Tries the configured LLM first; always falls back to the offline heuristic.
    Parsed materials get SMILES gap-fill + a controlled-chemical screen via the
    ChemCrow gateway (no-op offline).
    """
    result = _llm_parse(text)
    if result is None:
        result = _offline_parse(text)
    if result.requirement.materials:
        from . import chemtools

        try:
            result.warnings.extend(
                chemtools.enrich_material_specs(result.requirement.materials)
            )
        except Exception as exc:
            log_handled_exception(logger, exc, "material enrichment failed")
    return result
