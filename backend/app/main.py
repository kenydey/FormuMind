"""FastAPI application entrypoint — the central gateway.

Mounts the research / DOE / optimize / tasks / metadata routers and configures
CORS for the Vite frontend.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import doe, experiments, formulations, optimize, research, tasks
from .api import search as search_router, ingest as ingest_router, chat as chat_router, settings as settings_router
from .config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Optionally enrich the raw-material library via PubChem (opt-in, best-effort)."""
    if settings.enrich_compounds:
        try:  # pragma: no cover - opt-in network path
            from .domain.knowledge import RAW_MATERIALS
            from .services.compounds import enrich_materials

            enrich_materials(RAW_MATERIALS)
        except Exception:
            pass
    yield


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


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "llm": "claude" if settings.anthropic_api_key else "offline-fallback",
        "celery_eager": settings.celery_eager,
    }
