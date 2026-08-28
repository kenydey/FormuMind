"""FastAPI application entrypoint — the central gateway.

Mounts the research / DOE / optimize / tasks / metadata routers and configures
CORS for the Vite frontend.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .services.errors import log_handled_exception, optional_import

from .api import auth as auth_router
from .api import chemistry as chemistry_router
from .api import doe, experiments, formulations, optimize, research, tasks
from .api import search as search_router, ingest as ingest_router, chat as chat_router, settings as settings_router
from .api import qc as qc_router
from .api import ip_analysis as ip_router
from .api import loop as loop_router
from .api import design as design_router
from .api import intent as intent_router
from .api import agents as agents_router
from .api import dependencies as dependencies_router
from .api import kb as kb_router
from .api import materials as materials_router
from .api import kg as kg_router
from .api import notebooklm as notebooklm_router
from .api import meta as meta_router
from .api import projects as projects_router
from .config import get_settings
from .middleware.api_auth import install_api_auth
from .middleware.rate_limit import RateLimitMiddleware

logger = logging.getLogger(__name__)
settings = get_settings()

_SKIP_BOOTSTRAP_ENV = "FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP"


def _skip_lifespan_bootstrap() -> bool:
    """Return True when FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP is truthy.

    Test-speedup flag ONLY: when set ("1"/"true"/"yes", case-insensitive),
    the lifespan manager skips the heavy bootstrap steps (secrets
    reload_settings, ColBERT seed-corpus bootstrap, PubChem enrichment).
    Default (unset/falsy) behaviour is completely unchanged — never set this
    in production.
    """
    return os.environ.get(_SKIP_BOOTSTRAP_ENV, "").strip().lower() in {"1", "true", "yes"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bootstrap ColBERT seed corpus and optional PubChem enrichment.

    Honours FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP (test-only fast path, see
    _skip_lifespan_bootstrap) to skip the heavy bootstrap steps while keeping
    the lightweight ELN store branch and shutdown semantics intact.
    """
    skip_bootstrap = _skip_lifespan_bootstrap()
    # ------------------------------------------------------------------
    # Fail-fast: ensure the API token is resolvable at startup so a missing
    # FORMUMIND_API_TOKEN surfaces immediately in production instead of on
    # the first authenticated request.
    # ------------------------------------------------------------------
    if settings.api_auth_enabled:
        from .middleware.api_auth import resolve_api_token

        resolve_api_token(settings)
    # ------------------------------------------------------------------
    # Recover stalled outbox rows (best-effort, must not block startup).
    # ------------------------------------------------------------------
    try:
        from .db import dispatcher
        from .db.database import default_session_factory
        from .db.sqlite_lock import sqlite_write_lock

        factory = default_session_factory()
        with sqlite_write_lock(settings.redis_url):
            with factory() as session:
                recovered = dispatcher.recover_stalled(session)
                if recovered:
                    logger.info(
                        "lifespan: recovered %d stalled outbox row(s)", recovered
                    )
                session.commit()
    except Exception:
        logger.exception("lifespan: outbox stall recovery failed (non-fatal)")
    if not skip_bootstrap:
        try:
            from .services.secrets_store import reload_settings

            reload_settings()
        except Exception as exc:
            log_handled_exception(logger, exc, "lifespan: reload_settings failed")
        try:
            from .services import colbert_store

            backend = colbert_store.active_backend()
            if backend == "colbert" or backend == "pylate":
                colbert_store.bootstrap_seed_corpus()
            else:
                logger.info("lifespan: skipping ColBERT bootstrap (backend=%s)", backend)
        except Exception as exc:
            log_handled_exception(logger, exc, "lifespan: ColBERT bootstrap failed")
        if settings.material_store_enabled:
            try:
                from .db.material_store import get_material_store
                from .domain.knowledge import RAW_MATERIALS, _SEED_MATERIALS

                get_material_store().seed_missing(_SEED_MATERIALS)
                RAW_MATERIALS.refresh()
            except Exception as exc:
                log_handled_exception(logger, exc, "lifespan: material seed failed")
        if settings.enrich_compounds:
            try:  # pragma: no cover - opt-in network path
                from .domain.knowledge import RAW_MATERIALS
                from .services.compounds import enrich_materials

                enriched = enrich_materials(RAW_MATERIALS)
                # enrich_materials mutates the spec dicts in place; persist so
                # the backfilled SMILES survive a restart instead of costing a
                # PubChem round-trip on every boot.
                if enriched and settings.material_store_enabled:
                    RAW_MATERIALS.persist_all()
            except Exception as exc:
                log_handled_exception(logger, exc, "lifespan: PubChem enrichment failed")
    try:
        from .db.campaign_store import get_campaign_store
        from .db.store import get_experiment_store

        if settings.campaign_backend.lower() == "datalab" or settings.experiment_backend.lower() == "datalab":
            get_campaign_store(settings)
            get_experiment_store(settings)
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan: ELN store initialization failed", level=logging.ERROR)
    # RAG 冷启动预热（非阻塞，2s 后后台触发）
    if not skip_bootstrap:
        try:
            import asyncio as _asyncio

            from .services.rag_preheat import preheat as _rag_prewarm

            loop = _asyncio.get_running_loop()

            def _schedule():
                _rag_prewarm(background=True)

            loop.call_later(2.0, _schedule)
            logger.info("lifespan: scheduled rag prewarm in 2s")
        except Exception as exc:
            log_handled_exception(logger, exc, "lifespan: schedule rag prewarm failed")
    yield
    try:
        from .db.campaign_store import get_campaign_store
        from .db.store import get_experiment_store

        if not skip_bootstrap:
            from .services.secrets_store import reload_settings

            reload_settings()
        await get_campaign_store().close()
        get_experiment_store().close()
    except Exception as exc:
        log_handled_exception(logger, exc, "lifespan shutdown: store close failed")


app = FastAPI(
    title="FormuMind",
    description="AI-assisted formulation R&D platform for metal surface treatment "
    "(anti-corrosion coatings, degreasers, surface treatment agents).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
install_api_auth(app)

app.include_router(research.router)
app.include_router(doe.router)
app.include_router(optimize.router)
app.include_router(tasks.router)
app.include_router(formulations.router)
app.include_router(experiments.router)
app.include_router(search_router.router, prefix="/api")
app.include_router(ingest_router.router, prefix="/api")
app.include_router(chat_router.router, prefix="/api")
app.include_router(kb_router.router, prefix="/api")
app.include_router(materials_router.router, prefix="/api")
app.include_router(kg_router.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(qc_router.router, prefix="/api")
app.include_router(ip_router.router)
app.include_router(loop_router.router)
app.include_router(design_router.router)
app.include_router(intent_router.router)
app.include_router(agents_router.router)
app.include_router(dependencies_router.router)
app.include_router(notebooklm_router.router)
app.include_router(chemistry_router.router)
app.include_router(projects_router.router)
app.include_router(meta_router.router)
app.include_router(auth_router.router)


from .db.datalab_client import DatalabUnavailableError, check_datalab_reachable


@app.exception_handler(DatalabUnavailableError)
async def datalab_unavailable_handler(_request: Request, exc: DatalabUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Log 422 validation errors so intermittent autosave failures are traceable.

    The default handler silently returns 422 without logging *which* field failed,
    which made the project-autosave 422s impossible to diagnose. This keeps the
    exact same response body but records the field path + type + message for
    /api/projects (the noisy autosave path); other routes stay quiet.
    """
    if request.url.path.startswith("/api/projects"):
        errs = exc.errors()
        detail = [
            {
                "loc": ".".join(str(x) for x in e.get("loc", [])),
                "type": e.get("type"),
                "msg": e.get("msg"),
            }
            for e in errs
        ]
        logger.warning("PUT %s validation failed (%d errors): %s", request.url.path, len(errs), detail)
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Minimal liveness probe — no sensitive infrastructure details leaked.

    Only exposes status + coarse booleans needed for monitoring; URLs,
    error messages, and installed-extras inventory live in /health/detailed
    (which requires auth).
    """
    cfg = get_settings()

    datalab_ok, _ = check_datalab_reachable(cfg.datalab_api_url)
    datalab_required = (
        cfg.campaign_backend.lower() == "datalab"
        or cfg.experiment_backend.lower() == "datalab"
        or cfg.datalab_required
    )

    db_ok = True
    db_scheme = "postgresql" if cfg.db_url.startswith("postgresql") else "sqlite"
    try:
        from sqlalchemy import text

        from .db.database import default_session_factory

        with default_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    # Every async feature — research, optimize, inverse design, search — is
    # submitted through Celery, so an unreachable broker breaks all of them
    # while the process keeps answering requests. Reporting only the database
    # let that outage look healthy from the outside.
    from .api._dispatch import broker_reachable

    broker_ok = broker_reachable()

    # Which formats can actually be parsed. The image ships no optional
    # parsers, so a deployment can run for weeks accepting uploads and
    # indexing nothing at all — the same class of invisible outage as an
    # unreachable broker, and just as worth reporting here.
    from .services.parsing import format_availability

    try:
        formats = format_availability()
    except Exception:
        formats = {}
    pdf_ok = bool(formats.get("pdf"))

    overall = "ok"
    if not db_ok or not broker_ok or not pdf_ok or (datalab_required and not datalab_ok):
        overall = "degraded"

    return {
        "status": overall,
        "database": {"ok": db_ok, "scheme": db_scheme},
        # "required" is False in eager mode, where tasks run in-process.
        "task_broker": {"required": not cfg.celery_eager, "reachable": broker_ok},
        "parsers": formats,
        "datalab": {"required": datalab_required, "reachable": datalab_ok},
    }


def _mask_db_url(db_url: str) -> str:
    """Return scheme://host (password stripped) — safe to expose."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(db_url)
        host = parsed.hostname or ""
        if not host:
            return parsed.scheme or "db"
        return f"{parsed.scheme}://{host}"
    except Exception:
        return "db"


def _effective_provider() -> str:
    from .services.runtime_secrets import effective_setting

    return str(effective_setting(get_settings(), "llm_provider") or "")


def _vision_health() -> dict:
    """Which model would read a figure, and is it configured at all.

    ``vision_available()`` was unreachable over HTTP, so when a figure came back
    as a degraded placeholder there was no way to ask the server why. Reports
    *configured*, never *capable* — see that function's own docstring.
    """
    try:
        from .services.llm_roles import VISION, resolve_role
        from .services.vision_extract import vision_available

        cfg = resolve_role(VISION)
        configured, hint = vision_available()
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "inherits": cfg.inherited,
            "configured": configured,
            "hint": hint,
        }
    except Exception as exc:  # a diagnostic endpoint must not become the failure
        return {"configured": False, "hint": f"探测失败：{str(exc)[:120]}"}


@app.get("/health/detailed", tags=["meta"])
def health_detailed() -> dict:
    """Detailed infra snapshot — behind auth (not in public paths)."""
    cfg = get_settings()

    def _ok(pkg: str) -> bool:
        return optional_import(pkg)

    # Test-only fast path: probing optional extras imports heavy packages
    # (sentence_transformers/torch, paperqa, chemcrow ≈ 20+ s). Under the
    # FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP flag, report them as unprobed instead.
    skip_probe = _skip_lifespan_bootstrap()

    llm_key = cfg.get_active_api_key()
    datalab_ok, datalab_reason = check_datalab_reachable(cfg.datalab_api_url)
    datalab_required = (
        cfg.campaign_backend.lower() == "datalab"
        or cfg.experiment_backend.lower() == "datalab"
        or cfg.datalab_required
    )

    db_ok = True
    db_scheme = "postgresql" if cfg.db_url.startswith("postgresql") else "sqlite"
    db_error: str | None = None
    try:
        from sqlalchemy import text

        from .db.database import default_session_factory

        with default_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    overall = "ok"
    if not db_ok or (datalab_required and not datalab_ok):
        overall = "degraded"

    return {
        "status": overall,
        "app": cfg.app_name,
        "environment": cfg.environment,
        # Through the overlay, not off Settings: a provider switched in the UI
        # lives there, so reading the raw field reported the stale compiled
        # default and made this endpoint useless for the one question people ask
        # it — "which model am I actually talking to".
        "llm": _effective_provider() if llm_key else "offline-fallback",
        "llm_key_set": bool(llm_key),
        "vision": _vision_health(),
        "api_auth_enabled": cfg.api_auth_enabled,
        "celery_eager": cfg.celery_eager,
        "agent_bus": cfg.agent_bus_enabled,
        "database": {
            "ok": db_ok,
            "scheme": db_scheme,
            "url": _mask_db_url(cfg.db_url),
            "error": db_error,
        },
        "datalab": {
            "required": datalab_required,
            "reachable": datalab_ok,
            "url": cfg.datalab_api_url,
            "message": datalab_reason,
            "campaign_backend": cfg.campaign_backend,
            "experiment_backend": cfg.experiment_backend,
        },
        "installed_extras": (
            {pkg: None for pkg in ("chemcrow", "paperqa", "patent_client", "sentence_transformers", "rdkit", "psycopg2")}
            if skip_probe
            else {
                "chemcrow": _ok("chemcrow"),
                "paperqa": _ok("paperqa"),
                "patent_client": _ok("patent_client"),
                "sentence_transformers": _ok("sentence_transformers"),
                "rdkit": _ok("rdkit"),
                "psycopg2": _ok("psycopg2"),
            }
        ),
    }
