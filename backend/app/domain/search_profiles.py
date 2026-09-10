"""Domain search profiles — taxonomy + lexical + source policy per ProductDomain.

Frozen per-domain profiles drive Pull filters, Sanitize gates, and source_policy
routing (2026-09-10: arXiv off by default; ChemRxiv via OpenAlex source channel).
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

# ChemRxiv repository on OpenAlex (chemistry preprint channel).
CHEMRXIV_OPENALEX_SOURCE_ID = "S4393918830"

# Preferred OA-friendly coating / corrosion / materials journals (soft boost).
_PREFERRED_COATING_JOURNALS: tuple[str, ...] = (
    "S41155759",    # Progress in Organic Coatings
    "S96167972",    # Corrosion Science
    "S76010543",    # Surface and Coatings Technology
    "S164001016",   # ACS Applied Materials & Interfaces
    "S2481244646",  # RSC Advances
    "S4210172278",  # Journal of Coatings Technology and Research
)


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
    preferred_openalex_source_ids: tuple[str, ...] = ()
    chemrxiv_openalex_source_id: str = CHEMRXIV_OPENALEX_SOURCE_ID


# Shared source policy (2026-09-10): ChemRxiv replaces default arXiv preprint role.
_DEFAULT_SOURCE_POLICY: Mapping[str, str] = {
    "arxiv": "off",
    "openalex": "primary",
    "chemrxiv": "primary",
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
        preferred_openalex_source_ids=_PREFERRED_COATING_JOURNALS,
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
        preferred_openalex_source_ids=_PREFERRED_COATING_JOURNALS,
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
        preferred_openalex_source_ids=_PREFERRED_COATING_JOURNALS,
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


def openalex_join_filters(*parts: str) -> str:
    """AND-join non-empty OpenAlex filter fragments with commas."""
    return ",".join(p.strip() for p in parts if p and p.strip())


def policy_tier(profile: DomainSearchProfile | None, key: str, *, default: str = "off") -> str:
    """Return primary|support|off for a provider key."""
    policy = (
        profile.source_policy if profile is not None else _DEFAULT_SOURCE_POLICY
    )
    raw = str(policy.get(key, default) or default).strip().lower()
    if raw in {"primary", "support", "off"}:
        return raw
    return default


def policy_allows(profile: DomainSearchProfile | None, key: str, *, default: str = "off") -> bool:
    return policy_tier(profile, key, default=default) != "off"


def policy_page_size(profile: DomainSearchProfile | None, key: str, base: int, *, default: str = "off") -> int:
    """Shrink support-tier page size (~1/3); off → 0."""
    tier = policy_tier(profile, key, default=default)
    if tier == "off":
        return 0
    if tier == "support":
        return max(1, int(base * 0.33))
    return max(1, int(base))


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

