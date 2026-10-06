"""A throwaway knowledge base as the process default, restored afterwards.

The retrieval code reaches its database through process-wide singletons (the default engine, the chunk and source stores,
the settings cache), so an evaluation that must not touch a developer's real data has to swap all of them, and put them
back. ``isolated_kb`` does that; the CLI runs in a process of its own and the tests use it around each run.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# What the evaluation needs to be true, whatever the developer's .env says.
_FORCED = {
    "FORMUMIND_API_AUTH_ENABLED": "false",
    "FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP": "1",
    "FORMUMIND_KB_INGEST_AUTO": "false",
    "FORMUMIND_CELERY_EAGER": "true",
    "FORMUMIND_NEO4J_ENABLED": "false",
    "FORMUMIND_KB_V2_ENABLED": "true",
}


def _reset_singletons() -> None:
    from app.config import get_settings
    from app.db import chunk_store, source_store

    get_settings.cache_clear()
    chunk_store._store = None
    source_store._store = None
    try:
        from app.services import hybrid_search

        hybrid_search.reset_latency_stats()
    except Exception:  # pragma: no cover - a stats reset must never stop an evaluation
        pass


def _dispose_default_engine() -> None:
    from app.db import database

    with database._default_lock:
        engine = database._default.pop("engine", None)
        database._default.clear()
    if engine is not None:
        engine.dispose()


@contextmanager
def isolated_kb(workdir: Path | None = None) -> Iterator[Path]:
    """Point the application's default database at an empty file for the duration; yields its directory."""
    saved = {key: os.environ.get(key) for key in (*_FORCED, "FORMUMIND_DB_URL")}
    owned = None
    if workdir is None:
        owned = tempfile.TemporaryDirectory(prefix="formumind-eval-")
        workdir = Path(owned.name)
    try:
        os.environ.update(_FORCED)
        os.environ["FORMUMIND_DB_URL"] = f"sqlite:///{(workdir / 'eval.db').as_posix()}"
        _dispose_default_engine()
        _reset_singletons()
        yield workdir
    finally:
        _dispose_default_engine()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _reset_singletons()
        if owned is not None:
            try:
                owned.cleanup()
            except OSError:  # a Windows runner may still hold the file for a moment; the temp dir is not worth failing for
                pass
