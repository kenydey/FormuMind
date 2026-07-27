"""Material-space endpoints — the raw-material catalog behind formulation search.

GET  /api/materials              — list / filter the catalog
POST /api/materials              — add or update one material (auto-enriched)
POST /api/materials/promote      — promote harvested trade products into it
POST /api/materials/availability — flag supply status (drives substitution)

The catalog used to be a module literal; making it data is what lets ingredient
choice become a search variable, and lets the candidate pool grow from the
literature the user already ingests.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db.material_store import get_material_store
from ..domain.knowledge import RAW_MATERIALS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/materials", tags=["materials"])

_AVAILABILITY = ("in_stock", "restricted", "discontinued")


class MaterialSpec(BaseModel):
    """Editable material fields. Omitted keys are left untouched on update."""

    name: str
    role: str = ""
    formula: str | None = None
    smiles: str | None = None
    cas_no: str | None = None
    zh_name: str | None = None
    molar_mass: float | None = None
    price_cny_per_kg: float | None = None
    voc_contrib: float | None = None
    density_gcm3: float | None = None
    oil_absorption: float | None = None
    tg_k: float | None = None
    svhc: bool | None = None
    carrier: str | None = None
    functional_class: str | None = None
    equivalent_weight: float | None = None
    hansen_d: float | None = None
    hansen_p: float | None = None
    hansen_h: float | None = None
    hlb: float | None = None
    supplier: str | None = None
    lead_time_days: int | None = None
    availability: str = "in_stock"
    substitute_group: str | None = None
    # Fill blank CAS/SMILES/formula from the catalog → PubChem → chemtools
    # cascade before storing. Network-bound; opt out for bulk imports.
    enrich: bool = True


class MaterialView(BaseModel):
    name: str
    role: str = ""
    origin: str = "seed"
    availability: str = "in_stock"
    spec: dict = Field(default_factory=dict)


class MaterialListResponse(BaseModel):
    total: int
    materials: list[MaterialView]
    store_enabled: bool


class PromoteRequest(BaseModel):
    """Promote trade products harvested from ingested documents into materials."""

    limit: int = Field(default=50, ge=1, le=500)
    min_mentions: int = Field(default=2, ge=1)
    role: str = ""


class PromoteResult(BaseModel):
    promoted: int
    skipped: int
    names: list[str] = Field(default_factory=list)


class AvailabilityRequest(BaseModel):
    name: str
    availability: str


def _require_store():
    if not get_settings().material_store_enabled:
        raise HTTPException(
            status_code=409,
            detail="材料空间未启用（FORMUMIND_MATERIAL_STORE_ENABLED）",
        )
    return get_material_store()


def _to_view(name: str, spec: dict) -> MaterialView:
    return MaterialView(
        name=name,
        role=str(spec.get("role") or ""),
        origin=str(spec.get("origin") or "seed"),
        availability=str(spec.get("availability") or "in_stock"),
        spec=spec,
    )


@router.get("", response_model=MaterialListResponse)
def list_materials(
    q: str = Query(default=""),
    role: str = Query(default=""),
    availability: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
) -> MaterialListResponse:
    """The full catalog as the formulation engine sees it (seed + additions)."""
    settings = get_settings()
    term, role_f, avail_f = q.strip().lower(), role.strip(), availability.strip()
    views: list[MaterialView] = []
    for name, spec in RAW_MATERIALS.items():
        if role_f and spec.get("role") != role_f:
            continue
        if avail_f and (spec.get("availability") or "in_stock") != avail_f:
            continue
        if term and term not in name.lower() and term not in str(spec.get("zh_name") or "").lower():
            continue
        views.append(_to_view(name, spec))
        if len(views) >= limit:
            break
    return MaterialListResponse(
        total=len(views),
        materials=views,
        store_enabled=settings.material_store_enabled,
    )


@router.post("", response_model=MaterialView)
def upsert_material(body: MaterialSpec) -> MaterialView:
    """Add or update a material; it is immediately usable by DOE/optimize."""
    store = _require_store()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    if body.availability not in _AVAILABILITY:
        raise HTTPException(
            status_code=400, detail=f"availability 必须是 {_AVAILABILITY} 之一"
        )

    spec = body.model_dump(exclude={"name", "enrich"}, exclude_none=True)
    if body.enrich and not (spec.get("cas_no") and spec.get("smiles")):
        spec.update(_enrich_spec(name, spec))

    if not store.upsert(name, spec, origin="user", overwrite=True):
        raise HTTPException(status_code=500, detail="材料写入失败")
    RAW_MATERIALS.refresh()
    return _to_view(name, RAW_MATERIALS.get(name, spec))


def _enrich_spec(name: str, spec: dict) -> dict:
    """Fill blank identity fields via the existing resolution cascade."""
    try:
        from ..services.chemical_lookup import lookup_chemical

        found = lookup_chemical(name) or {}
    except Exception as exc:  # pragma: no cover - network/optional path
        logger.debug("material enrich failed for %s: %s", name, exc)
        return {}
    out: dict = {}
    for key in ("cas_no", "smiles", "formula", "zh_name", "molar_mass"):
        if not spec.get(key) and found.get(key):
            out[key] = found[key]
    return out


@router.post("/promote", response_model=PromoteResult)
def promote_products(body: PromoteRequest) -> PromoteResult:
    """Promote commercial products harvested from ingested literature.

    ``kb_products`` already carries trade_name/supplier/cas/smiles/role for
    every product seen in the corpus — this turns that registry into usable
    formulation candidates, which is how the catalog grows past the curated
    seed without manual data entry.
    """
    store = _require_store()
    try:
        from ..db.product_store import get_product_store

        products = get_product_store().search(limit=body.limit * 4)
    except Exception as exc:
        logger.warning("promote: product registry unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="产品登记簿不可用") from exc

    promoted: list[str] = []
    skipped = 0
    for product in products:
        if len(promoted) >= body.limit:
            break
        role = (product.role or "").strip()
        if body.role and role != body.role:
            continue
        if (product.mention_count or 0) < body.min_mentions:
            skipped += 1
            continue
        name = " ".join(x for x in (product.trade_name, product.grade) if x).strip()
        if not name or name in RAW_MATERIALS:
            skipped += 1
            continue
        spec = {
            "role": role or "additive",
            "cas_no": product.cas or None,
            "smiles": product.smiles,
            "zh_name": product.generic_name or None,
            "supplier": product.supplier or None,
        }
        # overwrite=False: a harvested row must never clobber curated chemistry.
        if store.upsert(name, spec, origin="kb_promoted", overwrite=False):
            promoted.append(name)
        else:
            skipped += 1
    if promoted:
        RAW_MATERIALS.refresh()
    return PromoteResult(promoted=len(promoted), skipped=skipped, names=promoted)


@router.post("/availability", response_model=MaterialView)
def set_availability(body: AvailabilityRequest) -> MaterialView:
    """Flag supply status. ``discontinued`` is the substitution trigger."""
    store = _require_store()
    if body.availability not in _AVAILABILITY:
        raise HTTPException(
            status_code=400, detail=f"availability 必须是 {_AVAILABILITY} 之一"
        )
    name = body.name.strip()
    if name not in RAW_MATERIALS:
        raise HTTPException(status_code=404, detail=f"未知材料：{name}")
    # Seed materials have no row until they're edited; upsert covers both.
    store.upsert(name, {"availability": body.availability}, origin="seed", overwrite=True)
    RAW_MATERIALS.refresh()
    return _to_view(name, RAW_MATERIALS.get(name, {}))
