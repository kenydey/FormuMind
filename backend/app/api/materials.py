"""Material-space endpoints — the raw-material catalog behind formulation search.

GET  /api/materials                 — list / filter the catalog
POST /api/materials                 — add or update one material (auto-enriched)
POST /api/materials/availability    — flag supply status (drives substitution)
POST /api/materials/import          — batch import (json/csv/xlsx)
GET  /api/materials/export          — batch export
GET  /api/materials/import-template — empty template download
GET/POST /api/materials/candidates  — pending promotion queue
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db.material_store import get_material_store
from ..domain.knowledge import RAW_MATERIALS
from ..domain.schemas import Formulation, Requirement

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
    enrich: bool = True


class MaterialView(BaseModel):
    name: str
    role: str = ""
    origin: str = "seed"
    availability: str = "in_stock"
    archived: bool = False
    spec: dict = Field(default_factory=dict)


class MaterialListResponse(BaseModel):
    total: int
    materials: list[MaterialView]
    store_enabled: bool


class AvailabilityRequest(BaseModel):
    name: str
    availability: str


class ArchiveRequest(BaseModel):
    name: str
    archived: bool = True


class ProposeRequest(BaseModel):
    name: str
    role: str = ""
    cas_no: str | None = None
    smiles: str | None = None
    formula: str | None = None
    zh_name: str | None = None
    supplier: str | None = None
    source: str = "formula"
    source_ref: str = ""


class ProposeManyRequest(BaseModel):
    materials: list[ProposeRequest] = Field(default_factory=list)
    source: str = "formula"
    source_ref: str = ""


class PromoteRequirementRequest(BaseModel):
    requirement: Requirement


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
        archived=bool(spec.get("archived")),
        spec=spec,
    )


@router.get("", response_model=MaterialListResponse, include_in_schema=True)
def list_materials(
    q: str = Query(default=""),
    role: str = Query(default=""),
    availability: str = Query(default=""),
    functional_class: str = Query(default=""),
    substitute_group: str = Query(default=""),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
) -> MaterialListResponse:
    settings = get_settings()
    term = q.strip().lower()
    role_f, avail_f = role.strip(), availability.strip()
    fc, sg = functional_class.strip(), substitute_group.strip()
    views: list[MaterialView] = []
    source_items = list(RAW_MATERIALS.items())
    if include_archived:
        try:
            store = get_material_store()
            for row in store.list_all():
                if not getattr(row, "archived", False):
                    continue
                if row.name in RAW_MATERIALS:
                    continue
                source_items.append((row.name, store.row_to_spec(row)))
        except Exception:
            pass
    for name, spec in source_items:
        if not include_archived and spec.get("archived"):
            continue
        if role_f and spec.get("role") != role_f:
            continue
        if avail_f and (spec.get("availability") or "in_stock") != avail_f:
            continue
        if fc and (spec.get("functional_class") or "") != fc:
            continue
        if sg and (spec.get("substitute_group") or "") != sg:
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
    try:
        from ..services.chemical_lookup import lookup_chemical

        found = lookup_chemical(name) or {}
    except Exception as exc:  # pragma: no cover
        logger.debug("material enrich failed for %s: %s", name, exc)
        return {}
    out: dict = {}
    for key in ("cas_no", "smiles", "formula", "zh_name", "molar_mass"):
        if not spec.get(key) and found.get(key):
            out[key] = found[key]
    return out


class SubstituteRequest(BaseModel):
    requirement: Requirement | None = None
    formulation: Formulation | None = None
    domain: str = ""
    slot_index: int | None = None
    material: str = ""
    limit: int = Field(default=10, ge=1, le=50)
    include_unavailable: bool = False
    include_external: bool = True
    external_limit: int = Field(default=8, ge=1, le=25)
    similarity_threshold: int = Field(default=85, ge=60, le=100)
    include_literature: bool = True
    literature_limit: int = Field(default=8, ge=1, le=25)
    include_surechembl: bool = True
    surechembl_limit: int = Field(default=8, ge=1, le=25)
    include_llm: bool | None = None
    llm_limit: int = Field(default=5, ge=1, le=15)


def _slot_candidates(genome) -> list[str]:
    return [s.material for s in genome.slots]


def _resolve_material_slot(genome, material: str) -> int | None:
    from difflib import get_close_matches

    needle = material.strip()
    if not needle:
        return None
    names = _slot_candidates(genome)
    folded = [n.casefold() for n in names]
    key = needle.casefold()

    exact = [i for i, f in enumerate(folded) if f == key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    contained = [i for i, f in enumerate(folded) if key in f or (f and f in key)]
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        return None

    close = get_close_matches(key, folded, n=3, cutoff=0.6)
    if len(close) == 1:
        return folded.index(close[0])
    return None


def _material_not_found(material: str, genome) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "message": f"配方中不含材料：{material}",
            "candidates": _slot_candidates(genome),
        },
    )


@router.post("/substitutes")
def substitutes(body: SubstituteRequest) -> dict:
    from ..domain.genome import genome_from_formulation
    from ..pipeline import reconstruct
    from ..services.substitution import find_substitutes

    req = body.requirement
    if body.formulation is not None:
        genome = genome_from_formulation(body.formulation)
    elif req is not None:
        genome = reconstruct.genome_from_requirement(req)
    else:
        raise HTTPException(status_code=400, detail="需提供 formulation 或 requirement")

    index = body.slot_index
    if index is None:
        if not body.material:
            raise HTTPException(status_code=400, detail="需提供 slot_index 或 material")
        index = _resolve_material_slot(genome, body.material)
        if index is None:
            raise _material_not_found(body.material, genome)
    if not 0 <= index < len(genome.slots):
        raise HTTPException(status_code=400, detail="slot_index 超出范围")

    try:
        return find_substitutes(
            genome,
            index,
            req,
            limit=body.limit,
            include_unavailable=body.include_unavailable,
            include_external=body.include_external,
            external_limit=body.external_limit,
            similarity_threshold=body.similarity_threshold,
            include_literature=body.include_literature,
            literature_limit=body.literature_limit,
            include_surechembl=body.include_surechembl,
            surechembl_limit=body.surechembl_limit,
            include_llm=body.include_llm,
            llm_limit=body.llm_limit,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("materials substitutes failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"材料替代分析失败：{exc}",
        ) from exc


@router.get("/supply-risk")
def supply_risk(domain: str = Query(default="")) -> dict:
    from ..domain.schemas import ProductDomain
    from ..pipeline import reconstruct
    from ..services.substitution import scan_supply_risk

    domains = [d for d in ProductDomain if not domain or d.value == domain]
    if domain and not domains:
        raise HTTPException(status_code=400, detail=f"未知产品域：{domain}")
    genomes = {}
    for product in domains:
        try:
            req = Requirement(domain=product)
            genomes[product.value] = reconstruct.genome_from_requirement(req)
        except Exception as exc:
            logger.debug("supply-risk: baseline for %s failed: %s", product, exc)
    return scan_supply_risk(genomes)


@router.post("/availability", response_model=MaterialView)
def set_availability(body: AvailabilityRequest) -> MaterialView:
    store = _require_store()
    if body.availability not in _AVAILABILITY:
        raise HTTPException(
            status_code=400, detail=f"availability 必须是 {_AVAILABILITY} 之一"
        )
    name = body.name.strip()
    if name not in RAW_MATERIALS:
        raise HTTPException(status_code=404, detail=f"未知材料：{name}")
    store.upsert(name, {"availability": body.availability}, origin="seed", overwrite=True)
    RAW_MATERIALS.refresh()
    return _to_view(name, RAW_MATERIALS.get(name, {}))


@router.post("/archive", response_model=MaterialView)
def archive_material(body: ArchiveRequest) -> MaterialView:
    store = _require_store()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    if not store.set_archived(name, body.archived):
        raise HTTPException(status_code=500, detail="归档失败")
    RAW_MATERIALS.refresh()
    if body.archived:
        row = store.get(name)
        spec = store.row_to_spec(row) if row else {"archived": True}
        return _to_view(name, spec)
    return _to_view(name, RAW_MATERIALS.get(name, {"archived": False}))


@router.post("/import")
async def import_materials(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=True),
) -> dict:
    _require_store()
    from ..services import material_io

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="空文件")
    try:
        records = material_io.detect_and_parse(file.filename or "", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    preview = (
        material_io.plan_import(records)
        if dry_run
        else material_io.commit_import(records, origin="import")
    )
    return {
        "dry_run": dry_run,
        "batch_id": preview.batch_id,
        "total": preview.total,
        "creates": preview.creates,
        "updates": preview.updates,
        "errors": preview.errors,
        "rows": [
            {
                "name": r.name,
                "action": r.action,
                "reason": r.reason,
                "matched_by": r.matched_by,
                "existing_name": r.existing_name,
            }
            for r in preview.rows[:200]
        ],
    }


@router.get("/export")
def export_materials(
    format: str = Query(default="csv"),
    q: str = Query(default=""),
    role: str = Query(default=""),
    availability: str = Query(default=""),
    functional_class: str = Query(default=""),
    substitute_group: str = Query(default=""),
    include_archived: bool = Query(default=False),
) -> Response:
    from ..services import material_io

    fmt = format.lower().strip()
    records = material_io.export_records(
        q=q,
        role=role,
        availability=availability,
        functional_class=functional_class,
        substitute_group=substitute_group,
        include_archived=include_archived,
    )
    try:
        if fmt == "json":
            body, media, filename = material_io.serialize_json(records), "application/json", "materials.json"
        elif fmt == "csv":
            body, media, filename = material_io.serialize_csv(records), "text/csv", "materials.csv"
        elif fmt in {"xlsx", "xls"}:
            body = material_io.serialize_xlsx(records)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = "materials.xlsx"
        else:
            raise HTTPException(status_code=400, detail="format 须为 json / csv / xlsx")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/import-template")
def import_template(format: str = Query(default="csv")) -> Response:
    from ..services import material_io

    try:
        body, media, filename = material_io.import_template(format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/candidates")
def list_candidates(limit: int = Query(default=200, ge=1, le=1000)) -> dict:
    _require_store()
    from ..services.material_promote import candidate_to_dict, get_candidate_store

    rows = get_candidate_store().list_pending(limit=limit)
    return {"total": len(rows), "candidates": [candidate_to_dict(r) for r in rows]}


@router.post("/candidates/dismiss-noise")
def dismiss_noise_endpoint(
    source: str = Query(default="kb_promoted"),
    limit: int = Query(default=2000, ge=1, le=5000),
) -> dict:
    """Dismiss pending KB junk that fails the chemistry-name gate."""
    _require_store()
    from ..services.material_promote import dismiss_noisy_candidates

    sources = [s.strip() for s in source.split(",") if s.strip()] or ["kb_promoted"]
    return dismiss_noisy_candidates(sources=sources, limit=limit)


@router.post("/candidates/{candidate_id}/promote")
def promote_candidate_endpoint(candidate_id: str) -> dict:
    _require_store()
    from ..services.material_promote import promote_candidate as _promote

    result = _promote(candidate_id)
    if not result.get("ok"):
        code = 404 if result.get("reason") == "not_found" else 500
        raise HTTPException(status_code=code, detail=result)
    return result


@router.post("/candidates/{candidate_id}/dismiss")
def dismiss_candidate_endpoint(candidate_id: str) -> dict:
    _require_store()
    from ..services.material_promote import dismiss_candidate as _dismiss

    result = _dismiss(candidate_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="候选不存在或更新失败")
    return result


@router.post("/propose")
def propose_one(body: ProposeRequest) -> dict:
    _require_store()
    from ..services.material_promote import propose_material

    return propose_material(
        body.name,
        body.model_dump(exclude={"name", "source", "source_ref"}, exclude_none=True),
        source=body.source,
        source_ref=body.source_ref,
    )


@router.post("/propose-many")
def propose_many_endpoint(body: ProposeManyRequest) -> dict:
    _require_store()
    from ..services.material_promote import propose_many

    items = [
        m.model_dump(exclude={"source", "source_ref"}, exclude_none=True)
        for m in body.materials
    ]
    return propose_many(items, source=body.source, source_ref=body.source_ref)


@router.post("/promote-from-requirement")
def promote_from_requirement(body: PromoteRequirementRequest) -> dict:
    _require_store()
    from ..services.material_promote import propose_from_requirement

    return propose_from_requirement(body.requirement)


@router.post("/harvest-kb-products")
def harvest_kb_products(
    limit: int = Query(default=200, ge=1, le=2000),
    min_mentions: int = Query(default=2, ge=1, le=50),
) -> dict:
    _require_store()
    from ..services.material_promote import promote_kb_products

    return promote_kb_products(limit=limit, min_mentions=min_mentions)
