"""Semi-automatic material catalog expansion.

High-confidence candidates (CAS / SMILES present) upsert into ``materials``
with ``origin=kb_promoted|requirement|formula|workbench``. Low-confidence
names may land in ``material_candidates`` for human promote / dismiss — but
**KB-sourced** bare names are gated hard to keep the pending queue usable.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..db.material_store import get_material_store, norm_key
from ..db.models import MaterialCandidateRow
from ..db.session_utils import commit_session
from ..domain.knowledge import RAW_MATERIALS
from ..services.errors import degrade_return

_HIGH_ORIGIN = {"kb_promoted", "requirement", "formula", "workbench", "user", "import"}
# Sources that must not flood the pending queue with extraction junk.
_STRICT_PENDING_SOURCES = {"kb_promoted"}

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")
_HAS_DIGIT = re.compile(r"\d")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9\-\s®™./]+$")

# Common false positives from patent/paper NER (places, orgs, UI chrome, glue words).
_NAME_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by",
    "from", "as", "at", "is", "are", "was", "were", "be", "this", "that", "these",
    "those", "fig", "figure", "table", "scheme", "example", "examples", "results",
    "method", "methods", "introduction", "conclusion", "abstract", "reference",
    "references", "supplementary", "supporting", "information", "copyright",
    "university", "institute", "laboratory", "department", "college", "school",
    "company", "companies", "corp", "corporation", "inc", "ltd", "llc", "gmbh",
    "co", "industries", "industry", "group", "holdings", "solutions", "technology",
    "technologies", "science", "sciences", "research", "center", "centre",
    "china", "chinese", "beijing", "shanghai", "guangzhou", "shenzhen", "japan",
    "tokyo", "usa", "uk", "germany", "france", "italy", "spain", "canada",
    "australia", "india", "korea", "taiwan", "hongkong", "singapore", "russia",
    "moscow", "akademgorodok", "corning", "imagej", "windows", "linux", "excel",
    "word", "powerpoint", "pdf", "http", "https", "www", "com", "org", "net",
    "ca", "us", "eu", "un", "who", "iso", "astm", "din", "gb",
}

# Short abbreviations that are real materials / resins (allowlist).
_CHEM_ABBREV = {
    "peg", "ppg", "pva", "pvc", "ptfe", "pet", "pu", "ep", "upa", "upa",
    "dgeba", "tdi", "mdi", "ipdi", "hdi", "bpa", "bpf", "hmee", "hme",
    "teos", "tmos", "gptms", "aptes", "hmds", "tmspma", "ipa", "mek", "mibk",
    "nmp", "dmf", "dmso", "thf", "toc", "voc", "uv", "led",
}

_CHEM_HINTS = (
    "epoxy", "resin", "silane", "siloxane", "acrylate", "methacrylate",
    "isocyanate", "urethane", "polyol", "amine", "amide", "anhydride",
    "phosphate", "phosphonate", "sulfate", "sulphate", "chloride", "oxide",
    "hydroxide", "carbonate", "nitrate", "fluoride", "bromide", "iodide",
    "glycol", "glycer", "phenol", "bisphenol", "melamine", "urea", "alkyd",
    "acrylic", "polyester", "polyurethane", "polyamide", "polyimide",
    "cellulose", "starch", "latex", "emulsion", "hardener", "curing",
    "crosslink", "catalyst", "inhibitor", "surfactant", "dispersant",
    "pigment", "filler", "solvent", "thinner", "monomer", "oligomer",
    "zinc", "titanium", "silicon", "aluminum", "aluminium", "iron", "copper",
    "nickel", "chrom", "molybd", "tungsten", "boron", "fluor",
    "树脂", "固化", "环氧", "硅烷", "丙烯酸", "异氰酸", "磷酸", "氧化",
    "锌", "钛", "硅", "铝", "铁", "铜", "溶剂", "颜料", "填料", "催化剂",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _has_identity(spec: dict[str, Any]) -> bool:
    cas = str(spec.get("cas_no") or "").strip()
    if cas and _CAS_RE.match(cas):
        return True
    return bool(str(spec.get("smiles") or "").strip())


def is_plausible_material_name(name: str) -> bool:
    """Reject NER junk that should never enter the pending queue."""
    display = (name or "").strip()
    if not display:
        return False
    key = norm_key(display)
    low = display.lower().strip()
    if not key or len(key) < 2:
        return False
    if key in _NAME_STOPWORDS or low in _NAME_STOPWORDS:
        return False
    if key in _CHEM_ABBREV:
        return True
    # Too short bare Latin tokens (CA, In, PEG without allowlist already handled).
    if len(key) <= 3 and not _HAS_CJK.search(display) and not _HAS_DIGIT.search(display):
        return False
    if len(display) < 3:
        return False
    tokens = [t for t in re.split(r"[\s,/|]+", low) if t]
    tokens = [re.sub(r"[^a-z0-9\u4e00-\u9fff\-]", "", t) for t in tokens]
    tokens = [t for t in tokens if t]
    if tokens and all(t in _NAME_STOPWORDS or norm_key(t) in _NAME_STOPWORDS for t in tokens):
        return False
    # Single Title-Case English word with no chemistry signal → likely place/brand chrome.
    if (
        _LATIN_WORD.match(display)
        and " " not in display
        and not _HAS_DIGIT.search(display)
        and len(key) <= 12
        and not any(h in low for h in _CHEM_HINTS)
        and not any(display.endswith(suf) for suf in ("ane", "ene", "ol", "ate", "ide", "ine", "ium", "yl"))
    ):
        # Allow trademarked grades like "Bayhydrol" (long enough + not stopword).
        if len(key) < 8:
            return False
    # Must look vaguely chemical: digit, CJK, chem hint, multi-token grade, or long name.
    if _HAS_DIGIT.search(display) or _HAS_CJK.search(display):
        return True
    if any(h in low for h in _CHEM_HINTS):
        return True
    if " " in display or "-" in display or "/" in display:
        return len(key) >= 6
    return len(key) >= 10


def should_enqueue_low_confidence(source: str, name: str, spec: dict[str, Any] | None = None) -> bool:
    """Whether a non-CAS/SMILES proposal should enter the pending queue."""
    src = (source or "").strip()
    if src not in _STRICT_PENDING_SOURCES:
        # requirement / workbench / formula: still gate obvious junk.
        return is_plausible_material_name(name)
    # KB path: never enqueue without identity unless the name clearly looks chemical.
    if not is_plausible_material_name(name):
        return False
    key = norm_key(name)
    if key in _CHEM_ABBREV:
        return True
    # Extra: KB bare names need a chem hint or multi-token grade-like shape.
    low = (name or "").lower()
    if any(h in low for h in _CHEM_HINTS):
        return True
    if _HAS_DIGIT.search(name or "") or _HAS_CJK.search(name or ""):
        return True
    if " " in (name or "") or "-" in (name or ""):
        return len(key) >= 8
    return False


class MaterialCandidateStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_pending(
        self,
        name: str,
        spec: dict[str, Any],
        *,
        source: str,
        source_ref: str = "",
        confidence: str = "low",
    ) -> bool:
        display = (name or "").strip()
        if not display:
            return False
        key = norm_key(display)
        try:
            with commit_session(self._session_factory) as session:
                row = (
                    session.query(MaterialCandidateRow)
                    .filter(MaterialCandidateRow.norm_key == key)
                    .first()
                )
                if row is None:
                    row = MaterialCandidateRow(
                        id=str(uuid.uuid4()),
                        norm_key=key,
                        name=display[:200],
                        status="pending",
                        created_at=_utcnow(),
                    )
                    session.add(row)
                elif row.status == "dismissed":
                    # Re-open dismissed items when seen again from a new source.
                    row.status = "pending"
                elif row.status == "promoted":
                    return False
                for field in (
                    "role",
                    "cas_no",
                    "smiles",
                    "formula",
                    "zh_name",
                    "supplier",
                ):
                    val = spec.get(field)
                    if val in (None, ""):
                        continue
                    if not getattr(row, field, None):
                        setattr(row, field, str(val)[:200] if field != "smiles" else str(val))
                row.source = (source or "")[:64]
                if source_ref:
                    row.source_ref = source_ref[:512]
                row.confidence = confidence if confidence in {"high", "low"} else "low"
                row.updated_at = _utcnow()
        except IntegrityError:
            return False
        except Exception as exc:
            return degrade_return(logger, exc, f"candidate upsert failed: {display}", False)
        return True

    def list_pending(self, limit: int = 200) -> list[MaterialCandidateRow]:
        with self._session_factory() as session:
            return (
                session.query(MaterialCandidateRow)
                .filter(MaterialCandidateRow.status == "pending")
                .order_by(MaterialCandidateRow.updated_at.desc())
                .limit(limit)
                .all()
            )

    def get(self, candidate_id: str) -> MaterialCandidateRow | None:
        with self._session_factory() as session:
            return (
                session.query(MaterialCandidateRow)
                .filter(MaterialCandidateRow.id == candidate_id)
                .first()
            )

    def set_status(self, candidate_id: str, status: str) -> bool:
        try:
            with commit_session(self._session_factory) as session:
                row = (
                    session.query(MaterialCandidateRow)
                    .filter(MaterialCandidateRow.id == candidate_id)
                    .first()
                )
                if row is None:
                    return False
                row.status = status
                row.updated_at = _utcnow()
        except Exception as exc:
            return degrade_return(logger, exc, f"candidate status failed: {candidate_id}", False)
        return True


_candidates: MaterialCandidateStore | None = None


def get_candidate_store() -> MaterialCandidateStore:
    global _candidates
    if _candidates is None:
        from ..db.database import default_session_factory

        _candidates = MaterialCandidateStore(default_session_factory())
    return _candidates


def propose_material(
    name: str,
    spec: dict[str, Any] | None = None,
    *,
    source: str = "kb_promoted",
    source_ref: str = "",
    force_pending: bool = False,
    enqueue_low_confidence: bool | None = None,
) -> dict[str, Any]:
    """Promote high-confidence materials; optionally queue the rest for review.

    Returns ``{"action": "upsert"|"pending"|"skipped"|"exists", "name": ...}``.

    For ``source=kb_promoted``, low-confidence names are skipped by default
    unless they pass the chemistry-name quality gate (see
    ``should_enqueue_low_confidence``). Requirement / workbench / formula
    still enqueue plausible low-confidence names.
    """
    display = (name or "").strip()
    if not display:
        return {"action": "skipped", "name": "", "reason": "empty"}
    payload = dict(spec or {})
    payload.setdefault("role", payload.get("role") or "")
    # Already in live catalog?
    if display in RAW_MATERIALS or any(norm_key(k) == norm_key(display) for k in RAW_MATERIALS):
        return {"action": "exists", "name": display}

    high = (not force_pending) and _has_identity(payload)
    if high:
        store = get_material_store()
        origin = source if source in _HIGH_ORIGIN else "kb_promoted"
        ok = store.upsert(display, payload, origin=origin, overwrite=False)
        if ok:
            RAW_MATERIALS.refresh()
            return {"action": "upsert", "name": display, "origin": origin}
        return {"action": "skipped", "name": display, "reason": "upsert_failed"}

    allow_pending = (
        True
        if force_pending
        else (
            should_enqueue_low_confidence(source, display, payload)
            if enqueue_low_confidence is None
            else bool(enqueue_low_confidence) and is_plausible_material_name(display)
        )
    )
    if not allow_pending:
        return {"action": "skipped", "name": display, "reason": "low_confidence_gated"}

    cand = get_candidate_store()
    ok = cand.upsert_pending(
        display,
        payload,
        source=source,
        source_ref=source_ref,
        confidence="high" if _has_identity(payload) else "low",
    )
    return {
        "action": "pending" if ok else "skipped",
        "name": display,
        "reason": "" if ok else "candidate_failed",
    }


def promote_candidate(candidate_id: str) -> dict[str, Any]:
    cand = get_candidate_store()
    row = cand.get(candidate_id)
    if row is None:
        return {"ok": False, "reason": "not_found"}
    if row.status == "promoted":
        return {"ok": True, "action": "exists", "name": row.name}
    spec = {
        "role": row.role or "",
        "cas_no": row.cas_no or None,
        "smiles": row.smiles or None,
        "formula": row.formula or None,
        "zh_name": row.zh_name or None,
        "supplier": row.supplier or None,
        "availability": "in_stock",
    }
    store = get_material_store()
    origin = row.source if row.source in _HIGH_ORIGIN else "kb_promoted"
    ok = store.upsert(row.name, {k: v for k, v in spec.items() if v is not None}, origin=origin)
    if not ok:
        return {"ok": False, "reason": "upsert_failed"}
    cand.set_status(candidate_id, "promoted")
    RAW_MATERIALS.refresh()
    return {"ok": True, "action": "upsert", "name": row.name, "origin": origin}


def dismiss_candidate(candidate_id: str) -> dict[str, Any]:
    ok = get_candidate_store().set_status(candidate_id, "dismissed")
    return {"ok": ok}


def propose_many(
    items: Iterable[dict[str, Any]],
    *,
    source: str,
    source_ref: str = "",
    enqueue_low_confidence: bool | None = None,
) -> dict[str, int]:
    counts = {"upsert": 0, "pending": 0, "exists": 0, "skipped": 0}
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            counts["skipped"] += 1
            continue
        result = propose_material(
            name,
            item,
            source=source,
            source_ref=source_ref,
            enqueue_low_confidence=enqueue_low_confidence,
        )
        counts[result.get("action", "skipped")] = counts.get(result.get("action", "skipped"), 0) + 1
    return counts


def promote_kb_products(limit: int = 200, *, min_mentions: int = 2) -> dict[str, int]:
    """Harvest ``kb_products`` into the catalog / pending queue (strict).

    - CAS/SMILES → upsert
    - else only enqueue when name passes chemistry gate AND mention_count >= min_mentions
    """
    try:
        from ..db.product_store import get_product_store

        store = get_product_store()
        products = store.search("", limit=limit)
    except Exception as exc:
        logger.debug("promote_kb_products: product store unavailable ({})", exc)
        return {"upsert": 0, "pending": 0, "exists": 0, "skipped": 0}

    items: list[dict[str, Any]] = []
    for p in products:
        trade = getattr(p, "trade_name", "") or ""
        generic = getattr(p, "generic_name", "") or ""
        name = (generic or trade).strip()
        if not name:
            continue
        cas = getattr(p, "cas", "") or None
        smiles = getattr(p, "smiles", None)
        mentions = int(getattr(p, "mention_count", 0) or 0)
        has_id = bool(str(cas or "").strip() or str(smiles or "").strip())
        if not has_id and mentions < min_mentions:
            continue
        if not has_id and not is_plausible_material_name(name):
            continue
        items.append(
            {
                "name": name,
                "role": getattr(p, "role", "") or "",
                "cas_no": cas,
                "smiles": smiles,
                "supplier": getattr(p, "supplier", "") or None,
            }
        )
    # KB harvest: still use gated pending for low-confidence survivors.
    return propose_many(items, source="kb_promoted", source_ref="kb_products")


def dismiss_noisy_candidates(
    *,
    sources: Iterable[str] | None = None,
    limit: int = 2000,
) -> dict[str, int]:
    """Auto-dismiss pending rows that fail the chemistry-name gate.

    Defaults to cleaning ``kb_promoted`` noise; pass ``sources=None`` to scan all.
    """
    cand = get_candidate_store()
    rows = cand.list_pending(limit=limit)
    wanted = {s.strip() for s in (sources or ("kb_promoted",)) if s and s.strip()}
    dismissed = 0
    kept = 0
    for row in rows:
        if wanted and (row.source or "") not in wanted:
            kept += 1
            continue
        # Keep high-confidence identity even if the display name looks odd.
        if _has_identity(
            {"cas_no": row.cas_no, "smiles": row.smiles}
        ) or should_enqueue_low_confidence(row.source or "kb_promoted", row.name or ""):
            kept += 1
            continue
        if cand.set_status(row.id, "dismissed"):
            dismissed += 1
        else:
            kept += 1
    return {"dismissed": dismissed, "kept": kept, "scanned": len(rows)}


def candidate_to_dict(row: MaterialCandidateRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "role": row.role or "",
        "cas_no": row.cas_no or "",
        "smiles": row.smiles or "",
        "formula": row.formula or "",
        "zh_name": row.zh_name or "",
        "supplier": row.supplier or "",
        "source": row.source or "",
        "source_ref": row.source_ref or "",
        "confidence": row.confidence or "low",
        "status": row.status,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# Process / metric keys that are never materials — skip workbench promotion.
_PROCESS_KEY_SUFFIXES = (
    "_c",
    "_min",
    "_um",
    "_mm",
    "_s",
    "_h",
    "_hr",
    "_gsm",
    "_gpl",
    "_pct",
    "_kpa",
    "_mpa",
    "_rpm",
    "_ph",
)
_PROCESS_KEY_EXACT = {
    "temperature",
    "time",
    "ph",
    "voltage",
    "current",
    "status",
    "note",
    "tags",
    "round",
    "score",
}


def _looks_like_process_key(key: str) -> bool:
    k = (key or "").strip().lower()
    if not k or k in _PROCESS_KEY_EXACT:
        return True
    if any(k.endswith(suf) for suf in _PROCESS_KEY_SUFFIXES):
        return True
    if k.startswith("cure_") or k.startswith("bake_") or k.startswith("film_"):
        return True
    return False


def propose_from_requirement(
    requirement: Any,
    *,
    source_ref: str = "requirement.materials",
) -> dict[str, int]:
    """Promote ``Requirement.materials`` into the global catalog / pending queue."""
    mats = getattr(requirement, "materials", None)
    if mats is None and isinstance(requirement, dict):
        mats = requirement.get("materials")
    items: list[dict[str, Any]] = []
    for m in mats or []:
        if hasattr(m, "model_dump"):
            d = m.model_dump()
        elif isinstance(m, dict):
            d = dict(m)
        else:
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        items.append(d)
    if not items:
        return {"upsert": 0, "pending": 0, "exists": 0, "skipped": 0}
    return propose_many(items, source="requirement", source_ref=source_ref)


def propose_from_workbench_rows(
    rows: Iterable[Any],
    *,
    campaign_id: int | None = None,
) -> dict[str, int]:
    """Queue unknown planned/actual param keys from workbench rows as candidates.

    High-confidence identity is rare on factor keys (usually bare names), so
    most land in the pending queue — which is intentional.
    """
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows or []:
        if hasattr(row, "model_dump"):
            data = row.model_dump()
        elif isinstance(row, dict):
            data = row
        else:
            data = {
                "planned_params": getattr(row, "planned_params", None) or {},
                "actual_params": getattr(row, "actual_params", None) or {},
            }
        for bag_name in ("planned_params", "actual_params"):
            bag = data.get(bag_name) or {}
            if not isinstance(bag, dict):
                continue
            for key in bag.keys():
                name = str(key or "").strip()
                if not name or _looks_like_process_key(name):
                    continue
                nk = norm_key(name)
                if nk in seen:
                    continue
                seen.add(nk)
                items.append({"name": name, "role": ""})
    ref = f"workbench:{campaign_id}" if campaign_id is not None else "workbench"
    return propose_many(items, source="workbench", source_ref=ref)


def safe_propose_from_requirement(requirement: Any, *, source_ref: str = "") -> dict[str, int]:
    try:
        return propose_from_requirement(requirement, source_ref=source_ref or "requirement.materials")
    except Exception as exc:
        logger.debug("propose_from_requirement failed: {}", exc)
        return {"upsert": 0, "pending": 0, "exists": 0, "skipped": 0}


def safe_propose_from_workbench_rows(
    rows: Iterable[Any],
    *,
    campaign_id: int | None = None,
) -> dict[str, int]:
    try:
        return propose_from_workbench_rows(rows, campaign_id=campaign_id)
    except Exception as exc:
        logger.debug("propose_from_workbench_rows failed: {}", exc)
        return {"upsert": 0, "pending": 0, "exists": 0, "skipped": 0}
