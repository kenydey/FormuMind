"""POST /api/search — Multi-source evidence retrieval (single-shot).
POST /api/search/stream — Incremental search; returns a task handle the client
     polls so it can render results while the search keeps going.
GET  /api/search/status — Per-source availability check (no network requests).
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from ..domain.schemas import Evidence, Requirement
from ..services import literature
from ..worker.tasks import run_search_task
from ._dispatch import submit

router = APIRouter()


class SearchRequest(BaseModel):
    query: str = ""
    source_types: list[str] = Field(default_factory=list)
    requirement: Requirement | None = None
    limit_per_source: int = Field(default=50, ge=1, le=200)
    total_limit: int = Field(default=300, ge=1, le=1000)
    notebooklm_notebook_id: str | None = None


def _effective_source_types(request_types: list[str]) -> list[str]:
    from ..config import get_settings

    if request_types:
        return request_types
    return list(get_settings().federated_sources)


def _assert_requirement_consistency(req: Requirement | None) -> None:
    """Block search when workspace requirement disagrees with the request body.

    Prevents a previous project's salt-spray / substrate settings from driving
    a new product-line literature pull. substrate is a hard block (wrong base
    metal = wrong formulation direction); salt_spray_hours is a soft annotation
    (a same-substrate retarget is legitimate, only surfaced for awareness).
    """
    if req is None or not req.project_id:
        return
    try:
        from ..db.project_store import get_project_store

        detail = get_project_store().get(req.project_id)
    except Exception:
        return
    if detail is None:
        return
    stored_domain = getattr(detail, "domain", None)
    ws = getattr(detail, "workspace", None)
    stored_req = None
    if ws is not None and getattr(ws, "requirement", None) is not None:
        stored_req = ws.requirement
        stored_domain = getattr(stored_req, "domain", stored_domain) or stored_domain
    if stored_domain is None:
        return

    def _norm(d) -> str:
        return d.value if hasattr(d, "value") else str(d)

    req_dom = _norm(req.domain) if req.domain else None
    stored_dom = _norm(stored_domain) if stored_domain else None
    if req_dom and stored_dom and req_dom != stored_dom:
        raise HTTPException(
            status_code=409,
            detail=(
                f"requirement.domain={req_dom} 与项目存储 domain={stored_dom} 不一致；"
                "请先在需求面板切换产品线并保存，再开始检索。"
            ),
        )

    # substrate hard-block: wrong base metal drives a wrong formulation direction.
    req_sub = _norm(req.substrate) if getattr(req, "substrate", None) else None
    stored_sub = _norm(stored_req.substrate) if stored_req and getattr(stored_req, "substrate", None) else None
    if req_sub and stored_sub and req_sub != stored_sub:
        raise HTTPException(
            status_code=409,
            detail=(
                f"requirement.substrate={req_sub} 与项目存储 substrate={stored_sub} 不一致；"
                "请先在需求面板切换基材并保存，再开始检索。"
            ),
        )

    # salt_spray_hours soft annotation: a same-substrate retarget is legitimate,
    # so we surface the mismatch for awareness rather than block the search.
    if stored_req is not None and getattr(stored_req, "salt_spray_hours", 0):
        req_salt = getattr(req, "salt_spray_hours", 0)
        if req_salt and float(req_salt) != float(stored_req.salt_spray_hours):
            import logging

            logging.getLogger(__name__).warning(
                "search requirement salt_spray_hours=%s differs from stored %s "
                "(soft: same-substrate retarget assumed legitimate)",
                req_salt,
                stored_req.salt_spray_hours,
            )


class TaskHandle(BaseModel):
    task_id: str
    stream_url: str
    status_url: str


class SourceStatus(BaseModel):
    available: bool
    offline_fallback: bool = False
    reason: str | None = None
    hint: str | None = None


class SearchResponse(BaseModel):
    evidence: list[Evidence]
    total: int
    source_status: dict[str, SourceStatus] = {}
    used_seed_fallback: bool = False
    filter_report: dict | None = None


def _used_seed_fallback(evidence: list[Evidence]) -> bool:
    return any(e.is_seed_corpus for e in evidence)


def _build_status() -> dict[str, SourceStatus]:
    raw = literature.get_source_availability()
    return {k: SourceStatus(**v) for k, v in raw.items()}


@router.get("/search/status")
def source_status() -> dict[str, SourceStatus]:
    """Lightweight availability check — no retrieval, no network requests.

    Called by the frontend on component mount so status badges appear before
    the user runs a search.
    """
    return _build_status()


@router.post("/search", response_model=SearchResponse, deprecated=True)
def search_sources(req: SearchRequest):
    """同步一次性检索（legacy）。前端请使用 ``POST /api/search/stream`` 增量检索。"""
    _assert_requirement_consistency(req.requirement)
    types = _effective_source_types(req.source_types)
    evidence, filter_report = literature.iter_search(
        query=req.query,
        source_types=types,
        req=req.requirement,
        total_limit=req.total_limit,
        per_source_cap=req.limit_per_source,
        notebooklm_notebook_id=req.notebooklm_notebook_id,
    )
    return SearchResponse(
        evidence=evidence,
        total=len(evidence),
        source_status=_build_status(),
        used_seed_fallback=_used_seed_fallback(evidence),
        filter_report=filter_report,
    )


@router.post("/search/stream", status_code=202)
def search_stream(req: SearchRequest) -> JSONResponse:
    _assert_requirement_consistency(req.requirement)
    return submit(run_search_task, {
        "query": req.query,
        "source_types": _effective_source_types(req.source_types),
        "requirement": req.requirement.model_dump() if req.requirement else None,
        "total_limit": req.total_limit,
        "per_source_cap": req.limit_per_source,
        "notebooklm_notebook_id": req.notebooklm_notebook_id,
    }, "search")

