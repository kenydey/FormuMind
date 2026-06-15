"""FastAPI application entrypoint — the central gateway.

Mounts the research / DOE / optimize / tasks / metadata routers and configures
CORS for the Vite frontend.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import doe, experiments, formulations, optimize, research, tasks
from .config import get_settings

settings = get_settings()

app = FastAPI(
    title="FormuMind",
    description="AI-assisted formulation R&D platform for metal surface treatment "
    "(anti-corrosion coatings, degreasers, surface treatment agents).",
    version="0.1.0",
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


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "llm": "claude" if settings.anthropic_api_key else "offline-fallback",
        "celery_eager": settings.celery_eager,
    }
