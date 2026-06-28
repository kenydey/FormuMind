"""FastAPI application entrypoint — the central gateway.

Mounts the research / DOE / optimize / tasks / metadata routers and configures
CORS for the Vite frontend.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import doe, experiments, formulations, optimize, research, tasks
from .api import search as search_router, ingest as ingest_router, chat as chat_router, settings as settings_router
from .api import qc as qc_router
from .api import ip_analysis as ip_router
from .api import process_optimize as process_router
from .api import loop as loop_router
from .api import intent as intent_router
from .api import agents as agents_router
from .api import dependencies as dependencies_router
from .api import notebooklm as notebooklm_router
from .api import projects as projects_router
from .config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bootstrap ColBERT seed corpus and optional PubChem enrichment."""
    try:
        from .services.secrets_store import reload_settings

        reload_settings()
    except Exception:
        pass
    try:
        from .services import colbert_store

        colbert_store.bootstrap_seed_corpus()
    except Exception:
        pass
    if settings.enrich_compounds:
        try:  # pragma: no cover - opt-in network path
            from .domain.knowledge import RAW_MATERIALS
            from .services.compounds import enrich_materials

            enrich_materials(RAW_MATERIALS)
        except Exception:
            pass
    try:
        from .db.campaign_store import get_campaign_store
        from .db.store import get_experiment_store

        if settings.campaign_backend.lower() == "datalab" or settings.experiment_backend.lower() == "datalab":
            get_campaign_store(settings)
            get_experiment_store(settings)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error("ELN store initialization failed: %s", exc)
    yield
    try:
        from .db.campaign_store import get_campaign_store
        from .db.store import get_experiment_store
        from .services.secrets_store import reload_settings

        reload_settings()
        await get_campaign_store().close()
        get_experiment_store().close()
    except Exception:
        pass


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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research.router)
app.include_router(doe.router)
app.include_router(optimize.router)
app.include_router(tasks.router)
app.include_router(formulations.router)
app.include_router(experiments.router)
app.include_router(search_router.router, prefix="/api")
app.include_router(ingest_router.router, prefix="/api")
app.include_router(chat_router.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(qc_router.router, prefix="/api")
app.include_router(ip_router.router)
app.include_router(process_router.router)
app.include_router(loop_router.router)
app.include_router(intent_router.router)
app.include_router(agents_router.router)
app.include_router(dependencies_router.router)
app.include_router(notebooklm_router.router)
app.include_router(projects_router.router)


from .db.datalab_client import DatalabUnavailableError, check_datalab_reachable


@app.exception_handler(DatalabUnavailableError)
async def datalab_unavailable_handler(_request: Request, exc: DatalabUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
def health() -> dict:
    cfg = get_settings()

    def _ok(pkg: str) -> bool:
        try:
            __import__(pkg)
            return True
        except Exception:
            return False

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
        "celery_eager": cfg.celery_eager,
        "agent_bus": cfg.agent_bus_enabled,
        "database": {
            "ok": db_ok,
            "scheme": db_scheme,
            "url": cfg.db_url.split("@")[-1] if "@" in cfg.db_url else cfg.db_url,
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
        "installed_extras": {
            "chemcrow": _ok("chemcrow"),
            "paperqa": _ok("paperqa"),
            "patent_client": _ok("patent_client"),
            "sentence_transformers": _ok("sentence_transformers"),
            "rdkit": _ok("rdkit"),
            "psycopg2": _ok("psycopg2"),
        },
    }
