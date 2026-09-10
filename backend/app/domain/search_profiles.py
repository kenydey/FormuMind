"""Domain search profiles — P0 taxonomy + lexical contracts per ProductDomain.

Q0 deliverable for KB quality: frozen per-domain profiles used by later slices
(arXiv / OpenAlex / S2 / CPC·IPC / ingest gates). No provider wiring here.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

from .schemas import ProductDomain

# OpenAlex concept IDs resolved via API (2026-09); keep as short IDs without URL prefix.
_OA_MATERIALS_SCIENCE = "C192562407"
_OA_CORROSION = "C20625102"
_OA_COATING = "C2781448156"
_OA_SURFACE_MODIFICATION = "C115537861"
_OA_METALLURGY = "C191897082"
_OA_PASSIVATION = "C33574316"
_OA_CLEANING_AGENT = "C165460524"
_OA_DEGREASING = "C150042643"
_OA_ELECTROCHEMISTRY = "C52859227"
_OA_NONIONIC_SURFACTANT = "C2994558038"


@dataclass(frozen=True)
class DomainSearchProfile:
    """Native taxonomy + lexical anchors for one ProductDomain."""

    domain: ProductDomain
    arxiv_categories: tuple[str, ...]
    openalex_concept_ids: tuple[str, ...]
    s2_fields_of_study: tuple[str, ...]
    cpc_prefixes: tuple[str, ...]
    ipc_codes: tuple[str, ...]
    keyword_allow: tuple[str, ...]
    keyword_deny: tuple[str, ...]
    source_policy: Mapping[str, str]


# Shared source policy defaults (P0). Scholar stays support; not hard-off.
_DEFAULT_SOURCE_POLICY: Mapping[str, str] = {
    "arxiv": "primary",
    "openalex": "primary",
    "semantic_scholar": "support",
    "epo": "primary",
    "google_patents": "primary",
    "surechembl": "support",
    "google_scholar": "support",
    "pubchem": "support",
}

# Cross-domain medical / off-topic noise (always deny for surface-chem R&D).
_COMMON_DENY: tuple[str, ...] = (
    "biomedical",
    "implant",
    "orthopedic",
    "orthopaedic",
    "dental implant",
    "pulmonary surfactant",
    "heusler",
    "hydrogen storage",
    "quasar",
    "interstellar",
)


_PROFILES: dict[ProductDomain, DomainSearchProfile] = {
    ProductDomain.anticorrosion_coating: DomainSearchProfile(
        domain=ProductDomain.anticorrosion_coating,
        arxiv_categories=(
            "cond-mat.mtrl-sci",
            "physics.chem-ph",
            "cond-mat.soft",
        ),
        openalex_concept_ids=(
            _OA_MATERIALS_SCIENCE,
            _OA_CORROSION,
            _OA_COATING,
            _OA_METALLURGY,
        ),
        # Drop medicine — primary medical noise vector for coating queries.
        s2_fields_of_study=(
            "materials science",
            "chemistry",
            "engineering",
        ),
        cpc_prefixes=("C09D", "C08G", "C23F"),
        ipc_codes=("C09D", "C09D175/04", "C08G18/00", "C23F"),
        keyword_allow=(
            "anticorrosion",
            "anti-corrosion",
            "corrosion",
            "coating",
            "paint",
            "primer",
            "epoxy",
            "polyurethane",
            "zinc-rich",
            "salt spray",
            "防腐",
            "防腐蚀",
            "防锈",
            "涂料",
            "涂层",
            "底漆",
            "环氧",
            "聚氨酯",
            "盐雾",
            "缓蚀",
        ),
        keyword_deny=_COMMON_DENY
        + (
            "battery electrode",
            "photovoltaic",
            "high-entropy alloy",
        ),
        source_policy=_DEFAULT_SOURCE_POLICY,
    ),
    ProductDomain.degreaser: DomainSearchProfile(
        domain=ProductDomain.degreaser,
        arxiv_categories=(
            "physics.chem-ph",
            "cond-mat.soft",
            "cond-mat.mtrl-sci",
        ),
        openalex_concept_ids=(
            _OA_CLEANING_AGENT,
            _OA_DEGREASING,
            _OA_NONIONIC_SURFACTANT,
            _OA_MATERIALS_SCIENCE,
            _OA_ELECTROCHEMISTRY,
        ),
        s2_fields_of_study=(
            "chemistry",
            "materials science",
            "engineering",
            "environmental science",
        ),
        cpc_prefixes=("C23G", "C11D", "C23F"),
        ipc_codes=("C23G", "C11D", "C23F"),
        keyword_allow=(
            "degreas",
            "cleaner",
            "cleaning",
            "alkaline",
            "surfactant",
            "detergent",
            "metal cleaning",
            "pretreatment",
            "脱脂",
            "清洗",
            "清洗剂",
            "碱性",
            "表面活性剂",
            "预处理",
            "除油",
        ),
        keyword_deny=_COMMON_DENY
        + (
            "laundry detergent",
            "dishwash",
            "cosmetic",
            "skin care",
        ),
        source_policy=_DEFAULT_SOURCE_POLICY,
    ),
    ProductDomain.surface_treatment: DomainSearchProfile(
        domain=ProductDomain.surface_treatment,
        arxiv_categories=(
            "cond-mat.mtrl-sci",
            "physics.chem-ph",
            "physics.app-ph",
        ),
        openalex_concept_ids=(
            _OA_SURFACE_MODIFICATION,
            _OA_PASSIVATION,
            _OA_CORROSION,
            _OA_MATERIALS_SCIENCE,
            _OA_METALLURGY,
        ),
        s2_fields_of_study=(
            "materials science",
            "chemistry",
            "engineering",
        ),
        cpc_prefixes=("C23C", "C23F", "C25D"),
        ipc_codes=("C23C", "C23F", "C25D"),
        keyword_allow=(
            "passivat",
            "phosphat",
            "conversion coating",
            "surface treatment",
            "pretreatment",
            "anodiz",
            "silane",
            "chrome-free",
            "钝化",
            "磷化",
            "转化膜",
            "表面处理",
            "预处理",
            "阳极氧化",
            "硅烷",
            "无铬",
        ),
        keyword_deny=_COMMON_DENY
        + (
            "biodegrad",
            "stent",
            "bone implant",
        ),
        source_policy=_DEFAULT_SOURCE_POLICY,
    ),
    ProductDomain.autodeposition_coating: DomainSearchProfile(
        domain=ProductDomain.autodeposition_coating,
        arxiv_categories=(
            "cond-mat.mtrl-sci",
            "physics.chem-ph",
            "cond-mat.soft",
        ),
        openalex_concept_ids=(
            _OA_COATING,
            _OA_ELECTROCHEMISTRY,
            _OA_MATERIALS_SCIENCE,
            _OA_CORROSION,
            _OA_SURFACE_MODIFICATION,
        ),
        s2_fields_of_study=(
            "materials science",
            "chemistry",
            "engineering",
        ),
        cpc_prefixes=("C09D", "C25D", "C23C"),
        ipc_codes=("C09D", "C25D", "C23C", "C09D175/04"),
        keyword_allow=(
            "autodeposition",
            "autophoretic",
            "acid-induced coagulation",
            "emulsion deposition",
            "自沉积",
            "自泳",
            "自泳漆",
            "酸致凝聚",
            "乳液",
            "电泳漆",  # adjacent process vocabulary; keep allow, not deny
        ),
        keyword_deny=_COMMON_DENY
        + (
            "powder coating spray gun",
            "hot-dip galvaniz",
        ),
        source_policy=_DEFAULT_SOURCE_POLICY,
    ),
}


def get_profile(domain: ProductDomain | str) -> DomainSearchProfile:
    """Return the frozen DomainSearchProfile for a ProductDomain.

    Accepts enum or its string value. Raises KeyError for unknown domains.
    """
    if isinstance(domain, str):
        domain = ProductDomain(domain)
    try:
        return _PROFILES[domain]
    except KeyError as exc:
        raise KeyError(f"no DomainSearchProfile for {domain!r}") from exc


def all_profiles() -> tuple[DomainSearchProfile, ...]:
    """All built-in profiles in ProductDomain declaration order."""
    return tuple(_PROFILES[d] for d in ProductDomain)


def arxiv_cat_clause(profile: DomainSearchProfile) -> str:
    """Build ``(cat:A OR cat:B …)`` for arXiv API query append."""
    cats = [c.strip() for c in profile.arxiv_categories if c and c.strip()]
    if not cats:
        return ""
    return "(" + " OR ".join(f"cat:{c}" for c in cats) + ")"


def openalex_concepts_filter(profile: DomainSearchProfile) -> str:
    """OpenAlex ``filter`` fragment: ``concepts.id:C1|C2|…``."""
    ids = [i.strip() for i in profile.openalex_concept_ids if i and i.strip()]
    if not ids:
        return ""
    return "concepts.id:" + "|".join(ids)


@lru_cache(maxsize=16)
def profile_for_domain_value(domain_value: str) -> DomainSearchProfile:
    """Cached lookup by string value (handy for API/query paths)."""
    return get_profile(domain_value)


def cpc_query_clause(profile: DomainSearchProfile) -> str:
    """Google-Patents-style CPC clause: ``CPC=(C09D OR C08G)``."""
    prefs = [p.strip().upper() for p in profile.cpc_prefixes if p and p.strip()]
    if not prefs:
        return ""
    return "CPC=(" + " OR ".join(prefs) + ")"


def profile_enabled() -> bool:
    """True when domain_profile_search flag is on (default True once wired)."""
    try:
        from ..config import get_settings
        return bool(getattr(get_settings(), "domain_profile_search", True))
    except Exception:
        return True


def resolve_profile(domain: ProductDomain | str | None):
    """Return profile when flag on and domain set; else None (legacy path)."""
    if domain is None or not profile_enabled():
        return None
    try:
        return get_profile(domain)
    except (KeyError, ValueError):
        return None

