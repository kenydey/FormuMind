"""Pytest bootstrap — disable API auth so legacy TestClient tests keep working."""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("FORMUMIND_API_AUTH_ENABLED", "false")
os.environ.setdefault("FORMUMIND_ENVIRONMENT", "test")
# Datalab 已非 TESTING 公开模式：legacy TestClient 测试不得直连真平台。
# Datalab 专项测试显式构造 store/override settings，不受此默认值影响。
# Product defaults are datalab + DATALAB_REQUIRED=true; CI/unit tests isolate
# on explicit sqlite (not a product "no ELN" mode).
os.environ.setdefault("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
os.environ.setdefault("FORMUMIND_EXPERIMENT_BACKEND", "sqlite")
os.environ.setdefault("FORMUMIND_DATALAB_REQUIRED", "false")
# Test speed-up: skip heavy lifespan bootstrap (ColBERT seed corpus, settings
# reload, PubChem enrichment). Default/production behaviour is unchanged.
os.environ.setdefault("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")
# Async KB ingest spawns background fetch threads after search tasks; keep the
# suite offline/deterministic — tests that exercise it enable it explicitly
# with stubbed fetchers.
os.environ.setdefault("FORMUMIND_KB_INGEST_AUTO", "false")
# Production defaults celery_eager=False (real worker + Redis). Offline CI has
# no broker — run tasks in-process so 202/SSE suites stay green. Tests that
# assert broker-down behaviour monkeypatch celery_eager=False explicitly.
os.environ.setdefault("FORMUMIND_CELERY_EAGER", "true")
# Settings persistence (LLM / secrets / env flags) writes a .env file; point it
# at a session-scoped temp file so tests never touch the repo-root .env.
os.environ.setdefault(
    "FORMUMIND_ENV_FILE",
    os.path.join(tempfile.mkdtemp(prefix="formumind-test-env-"), ".env"),
)
# The default database is ``sqlite:///./data/formumind.db`` — a CWD-relative file inside the checkout. Left
# alone, the suite (a) wrote its rows into a developer's real database, and (b) only passed
# ``test_api_auth`` when some earlier test happened to have created ``backend/data``: run on its own in a
# fresh clone, the startup check refused to boot ("SQLite data directory ... does not exist"). A session-scoped
# temp database makes both go away; tests that need their own file still set ``FORMUMIND_DB_URL`` themselves.
os.environ.setdefault(
    "FORMUMIND_DB_URL",
    "sqlite:///" + (Path(tempfile.mkdtemp(prefix="formumind-test-db-")) / "formumind.db").as_posix(),
)


# ── throwaway databases do not fsync ────────────────────────────────────────
# Measured on the Windows CI runner (scripts/probe_commit_cost.py): the same 300 DDL statements take 1175 ms with a commit each
# under synchronous=FULL (7.8 ms per fsync there), 89 ms under NORMAL and 49 ms under OFF. The Alembic-built fixtures run ~450
# statements, which was ~4.5 s of setup in every test that uses one. Nothing here asserts that a commit survives power loss;
# production keeps SQLite's default. Engine-wide, so it also reaches the engine Alembic builds for itself.
import sqlite3 as _sqlite3  # noqa: E402

from sqlalchemy import event as _sa_event  # noqa: E402
from sqlalchemy.engine import Engine as _Engine  # noqa: E402


@_sa_event.listens_for(_Engine, "connect")
def _throwaway_databases_do_not_fsync(dbapi_connection, _record):  # pragma: no cover - driver hook
    if isinstance(dbapi_connection, _sqlite3.Connection):
        dbapi_connection.execute("PRAGMA synchronous=OFF")


# ── hermetic network guard ──────────────────────────────────────────────────
# Every non-loopback connect fails fast (ENETUNREACH) - exactly the "offline" degrade the application code already handles -
# unless the test is marked ``@pytest.mark.network`` or FORMUMIND_TEST_ALLOW_NETWORK=1 is set. The mechanism (and why it
# exists) lives in ``_network_guard.py`` so the API fuzzer, which does not run under pytest, can install the same one.
from tests import _network_guard  # noqa: E402

_NETWORK_ALLOWED = False
_network_guard.install(lambda: _NETWORK_ALLOWED)


@pytest.fixture(autouse=True)
def _network_marker(request):
    """``@pytest.mark.network`` (or golden_eval, which talks to live LLMs) opts in."""
    global _NETWORK_ALLOWED
    _NETWORK_ALLOWED = bool(
        request.node.get_closest_marker("network") or request.node.get_closest_marker("golden_eval")
    )
    yield
    _NETWORK_ALLOWED = False


# Background work a test *starts* — Celery-eager jobs run in daemon threads named
# ``eager-<kind>-<id>``, wiki compiles in ``wiki-compile``, the workbench loop in
# ``workbench-loop`` — used to outlive the test and run while the next ones did. That is
# how an unrelated test that globally patches ``httpx.Client`` and asserts "never called"
# collected 16 PubChem lookups from a still-running inverse-design job (and why such
# failures depended on timing). Wait for them, bounded, before the next test starts.
_BACKGROUND_THREAD_PREFIXES = ("eager-", "wiki-compile", "workbench-loop")
_BACKGROUND_DRAIN_TIMEOUT_S = 60.0


@pytest.fixture(autouse=True)
def _drain_background_threads():
    # Deliberately does NOT depend on ``monkeypatch``: set up first, this fixture tears
    # down *last* — after the test's environment overrides are undone. Draining while
    # ``FORMUMIND_API_AUTH_ENABLED=true`` was still in the environment let the job's
    # ``get_settings()`` pin an auth-enabled Settings object that the next test inherited
    # (401s in an unrelated module).
    before = {t.ident for t in threading.enumerate()}
    yield
    deadline = time.monotonic() + _BACKGROUND_DRAIN_TIMEOUT_S
    for thread in threading.enumerate():
        if thread.ident in before or not thread.name.startswith(_BACKGROUND_THREAD_PREFIXES):
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
    try:
        from app.config import get_settings

        get_settings.cache_clear()  # nothing a job cached under the test's env may outlive it
    except Exception:
        pass


# Snapshot os.environ before any test module is imported. Some third-party
# libraries (magika via markitdown, litellm) call
# ``dotenv.load_dotenv(dotenv.find_dotenv())`` at import time, which dumps the
# repo-root .env into os.environ permanently. Test isolation requires the
# repo-root .env to stay out of os.environ (FORMUMIND_ENV_FILE points at a
# temp file for exactly this reason), so import-time additions are reverted
# in pytest_collection_finish below.
_OS_ENVIRON_BASELINE = dict(os.environ)


def _revert_environ_pollution() -> None:
    for key in list(os.environ):
        if key.startswith("FORMUMIND_") and key not in _OS_ENVIRON_BASELINE:
            del os.environ[key]
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def pytest_collection_finish(session):
    """Undo import-time os.environ pollution (see _OS_ENVIRON_BASELINE)."""
    _revert_environ_pollution()


# pytest keeps the running test's node id in ``os.environ["PYTEST_CURRENT_TEST"]``, and Windows refuses an environment
# variable longer than 32,767 characters: a parametrized test whose *parameter* is a 40 KB string failed with
# "ValueError: the environment variable is longer than 32767 characters" at setup - on Windows only, so Linux CI was
# green. Ordinary ids are under 300 characters; anything past this limit is a parameter that needs a ``pytest.param(id=)``.
MAX_NODEID_CHARS = 2000


def overlong_node_ids(items, limit: int = MAX_NODEID_CHARS) -> list[str]:
    return [f"{item.nodeid[:100]}... ({len(item.nodeid):,} characters)" for item in items if len(item.nodeid) > limit]


def pytest_collection_modifyitems(config, items):
    too_long = overlong_node_ids(items)
    if too_long:
        raise pytest.UsageError(
            f"{len(too_long)} test id(s) longer than {MAX_NODEID_CHARS} characters; give the huge parameter an explicit "
            "id with pytest.param(..., id='short-name') (Windows cannot hold the id in PYTEST_CURRENT_TEST):\n  "
            + "\n  ".join(too_long)
        )


def pytest_configure():
    """Keep Settings cache + Celery eager flag aligned with the test env.

    ``celery_app`` snapshots ``task_always_eager`` at import time. If Settings
    were cached earlier (or a test toggled the env), probe/dispatch can think
    eager is on while ``.delay()`` still talks to Redis and hangs until the
    10s dispatch timeout → false 503s in CI.
    """
    try:
        from app.config import get_settings

        get_settings.cache_clear()
        eager = bool(get_settings().celery_eager)
        from app.worker.celery_app import celery_app

        celery_app.conf.task_always_eager = eager
        celery_app.conf.task_eager_propagates = True
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_rate_limits_before_test():
    """Clear in-memory rate-limit buckets so tests start from a clean state."""
    try:
        from app.middleware.rate_limit import reset_rate_limits

        reset_rate_limits()
    except Exception:
        pass
    # Process-global state that carries *network outcomes* from one test to the next:
    # literature-provider circuit breakers (failures recorded by one test would open
    # the breaker and make later search tests skip their sources) and the result
    # caches for literature search, PubChem, SureChEMBL and external alternatives
    # (an identical query in a later test returned the earlier test's cached hits and
    # skipped per-source progress callbacks). Each test starts from a clean slate.
    for _mod, _fn in (
        ("app.services.provider_health", "reset_provider_health"),
        ("app.services.chemtools", "clear_cache"),
        ("app.services.surechembl_client", "clear_surechembl_cache"),
        ("app.services.external_alternatives", "clear_external_cache"),
        # An absent Redis opens a 30 s breaker (every Redis touch fails at once instead of paying for a refused
        # connection — ~2 s each on Windows). One test's "Redis is down" must not be the next one's.
        ("app.services.redis_breaker", "reset"),
        # The runtime overlay on Settings (secrets and LLM config written by the UI / API, or copied from Settings by
        # a lifespan that ran in an earlier test). ``effective_setting`` prefers it over the attribute, so a stale
        # entry silently overrides a later test's ``monkeypatch.setattr(get_settings(), ...)``: test_mineru_cloud's
        # "SDK missing" case reported a missing token when it ran after test_v03.
        ("app.services.runtime_secrets", "reset_runtime_secrets"),
    ):
        try:
            getattr(__import__(_mod, fromlist=[_fn]), _fn)()
        except Exception:
            pass
    try:
        from app.services import literature

        with literature._SEARCH_CACHE_LOCK:
            literature._SEARCH_CACHE.clear()
    except Exception:
        pass
    try:
        # No Redis runs in the suite. Linux refuses a loopback connection at once, but Windows takes ~2 s to give up
        # on it — so the first Redis touch of every test (the breaker is reset above) would cost the production
        # 1 s connect timeout. A real local Redis answers in well under a millisecond.
        from app.services import redis_breaker

        redis_breaker.CONNECT_TIMEOUT_S = 0.05
    except Exception:
        pass
    yield
    # Re-sync after tests that monkeypatch celery_eager / clear Settings cache.
    try:
        from app.config import get_settings
        from app.worker.celery_app import celery_app

        celery_app.conf.task_always_eager = bool(get_settings().celery_eager)
    except Exception:
        pass
    # Drop the cached Settings so env mutations via monkeypatch.setenv in one
    # test file cannot leak into the next (lru_cache otherwise holds a stale
    # object after monkeypatch reverts the env vars). Also revert os.environ
    # pollution from libraries that load_dotenv() at import time inside a test
    # (lazy imports); import-time pollution is handled in
    # pytest_collection_finish.
    _revert_environ_pollution()

