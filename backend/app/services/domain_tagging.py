"""P0 domain tagging + lexical match helpers for Evidence / ingest gates."""
from __future__ import annotations

from typing import Any, Literal

from ..domain.schemas import Evidence, ProductDomain
from ..domain.search_profiles import DomainSearchProfile, get_profile, resolve_profile

Match = Literal["strong", "weak", "none"]
Taxonomy = Literal["arxiv", "openalex", "chemrxiv", "cpc", "lexical", "none"]


def tag_evidence_domain(
    ev: Evidence,
    domain: ProductDomain | str | None,
    *,
    taxonomy_source: Taxonomy = "none",
    match: Match | None = None,
    extra_tags: list[str] | None = None,
) -> Evidence:
    """Mutate Evidence with domain_tags / domain_match / taxonomy_source."""
    if domain is None:
        return ev
    dom = domain.value if isinstance(domain, ProductDomain) else str(domain)
    tags = list(ev.domain_tags or [])
    if dom not in tags:
        tags.append(dom)
    for t in extra_tags or []:
        if t and t not in tags:
            tags.append(t)
    ev.domain_tags = tags
    if match is not None:
        ev.domain_match = match
    elif ev.domain_match is None:
        ev.domain_match = "weak"
    if taxonomy_source:
        ev.taxonomy_source = taxonomy_source
    return ev


def lexical_hits(text: str, profile: DomainSearchProfile) -> tuple[int, int]:
    """Return (allow_hits, deny_hits) using casefold substring match."""
    blob = (text or "").casefold()
    allow = sum(1 for w in profile.keyword_allow if w and w.casefold() in blob)
    deny = sum(1 for w in profile.keyword_deny if w and w.casefold() in blob)
    return allow, deny


def search_deny_hits(
    text: str,
    profile: DomainSearchProfile | None,
    *,
    extra: list[str] | tuple[str, ...] | None = None,
) -> int:
    """Count retrieval-stage deny term hits (Top-5 #2)."""
    if profile is None and not extra:
        return 0
    from ..domain.search_profiles import effective_search_deny

    blob = (text or "").casefold()
    terms = effective_search_deny(profile, extra=extra)
    return sum(1 for w in terms if w and w.casefold() in blob)


def score_domain_match(
    ev: Evidence,
    domain: ProductDomain | str | None,
    *,
    cpc_codes: list[str] | None = None,
    negative_terms: list[str] | tuple[str, ...] | None = None,
) -> Match:
    """Derive strong/weak/none from taxonomy tags + lexical allow/deny."""
    profile = resolve_profile(domain)
    if profile is None:
        return "none"
    text = f"{ev.title} {ev.snippet} {ev.identifier}"
    allow, deny = lexical_hits(text, profile)
    # Top-5 #2: retrieval deny (stronger than keyword_deny alone).
    try:
        from ..config import get_settings

        deny_on = bool(getattr(get_settings(), "kb_search_deny_enabled", True))
    except Exception:
        deny_on = True
    if deny_on:
        s_deny = search_deny_hits(text, profile, extra=negative_terms)
        deny = max(deny, s_deny)
    if deny and allow < 2:
        return "none"
    tags = " ".join(ev.domain_tags or []).upper()
    cpc_hit = False
    for pref in profile.cpc_prefixes:
        p = pref.upper()
        if p and (p in tags or any(str(c).upper().startswith(p) for c in (cpc_codes or []))):
            cpc_hit = True
            break
    tax = ev.taxonomy_source
    if tax in {"arxiv", "openalex", "chemrxiv"} and allow >= 0:
        return "strong" if allow >= 1 or tax else "weak"
    if cpc_hit:
        return "strong"
    if allow >= 2:
        return "strong"
    if allow >= 1:
        return "weak"
    return "none"


def apply_domain_match(
    ev: Evidence,
    domain: ProductDomain | str | None,
    *,
    cpc_codes: list[str] | None = None,
    negative_terms: list[str] | tuple[str, ...] | None = None,
) -> Evidence:
    """Compute and stamp domain_match on evidence."""
    if domain is None:
        return ev
    match = score_domain_match(
        ev, domain, cpc_codes=cpc_codes, negative_terms=negative_terms
    )
    tag_evidence_domain(ev, domain, match=match, taxonomy_source=ev.taxonomy_source or "lexical")
    if cpc_codes:
        extras = [f"cpc:{c}" for c in cpc_codes[:4]]
        tag_evidence_domain(ev, domain, extra_tags=extras)
    return ev


def domain_match_bonus(ev: Evidence) -> float:
    """Scoring delta used by search_scoring."""
    m = ev.domain_match
    if m == "strong":
        return 0.12
    if m == "weak":
        return 0.04
    if m == "none":
        return -0.08
    return 0.0


def search_deny_penalty(
    ev: Evidence,
    domain: ProductDomain | str | None = None,
    *,
    negative_terms: list[str] | tuple[str, ...] | None = None,
) -> float:
    """Extra ranking penalty when retrieval deny terms hit (Top-5 #2)."""
    try:
        from ..config import get_settings

        if not bool(getattr(get_settings(), "kb_search_deny_enabled", True)):
            return 0.0
    except Exception:
        return 0.0
    profile = resolve_profile(domain) if domain is not None else None
    # Prefer stamped domain when present.
    if profile is None and ev.domain_tags:
        profile = resolve_profile(ev.domain_tags[0])
    text = f"{ev.title} {ev.snippet} {ev.identifier}"
    hits = search_deny_hits(text, profile, extra=negative_terms)
    if hits <= 0:
        return 0.0
    # Cap so authority/entity boosts can still salvage strong allow hits.
    return -min(0.25, 0.08 * hits)


def profile_or_none(domain: Any) -> DomainSearchProfile | None:
    return resolve_profile(domain)
