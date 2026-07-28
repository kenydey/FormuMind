"""FastAPI application entrypoint — the central gateway.

Mounts the research / DOE / optimize / tasks / metadata routers and configures
CORS for the Vite frontend.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .services.errors import log_handled_exception, optional_import

from .api import auth as auth_router
from .api import chemistry as chemistry_router
from .api import doe, experiments, formulations, optimize, research, tasks
from .api import search as search_router, ingest as ingest_router, chat as chat_router, settings as settings_router
from .api import qc as qc_router
from .api import ip_analysis as ip_router
from .api import process_optimize as process_router
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

        factory = default_session_factory()
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

            colbert_store.bootstrap_seed_corpus()
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
app.include_router(process_router.router)
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

    overall = "ok"
    if not db_ok or (datalab_required and not datalab_ok):
        overall = "degraded"

    return {
        "status": overall,
        "database": {"ok": db_ok, "scheme": db_scheme},
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
        "llm": cfg.llm_provider if llm_key else "offline-fallback",
        "llm_key_set": bool(llm_key),
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
