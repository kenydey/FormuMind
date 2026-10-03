"""Pytest bootstrap — disable API auth so legacy TestClient tests keep working."""
from __future__ import annotations

import errno
import ipaddress
import os
import socket
import tempfile
import threading
import time

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


# ── hermetic network guard ──────────────────────────────────────────────────
# The suite used to reach the public internet: a tracer run counted ~330 real
# requests to PubChem (285, 117 from test_research_endpoint alone), OpenAlex,
# Semantic Scholar, SureChEMBL and DuckDuckGo, and a stream test sent a fake key
# to api.deepseek.com. Results then depended on third-party uptime and rate
# limits (a 429 from OpenAlex is just one of the ways that bites), and daemon
# threads left behind by one test kept calling out during later ones. Every
# non-loopback connect now fails fast (ENETUNREACH) — exactly the "offline"
# degrade the application code already handles — unless the test is marked
# ``@pytest.mark.network`` or FORMUMIND_TEST_ALLOW_NETWORK=1 is set.
_NETWORK_ALLOWED = False
_ALLOW_ALL_NETWORK = os.environ.get("FORMUMIND_TEST_ALLOW_NETWORK", "").strip().lower() in {"1", "true", "yes"}
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


def _is_local_address(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    return ip.is_loopback or ip.is_unspecified


def _blocked(address) -> bool:
    return not (_NETWORK_ALLOWED or _ALLOW_ALL_NETWORK or _is_local_address(address))


def _guarded_connect(self, address):
    if _blocked(address):
        raise OSError(
            errno.ENETUNREACH,
            f"outbound network is disabled in tests ({address[0]!r}); "
            "mock it, or mark the test with @pytest.mark.network",
        )
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    if _blocked(address):
        return errno.ENETUNREACH
    return _REAL_CONNECT_EX(self, address)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]


def _host_is_local(host: str | None) -> bool:
    return _is_local_address((host or "", 0))


def _assert_host_allowed(host: str | None) -> str | None:
    """Return an error message when a request to *host* must not leave the machine."""
    if _NETWORK_ALLOWED or _ALLOW_ALL_NETWORK or _host_is_local(host):
        return None
    return (
        f"outbound network is disabled in tests ({host!r}); "
        "mock it, or mark the test with @pytest.mark.network"
    )


# The socket layer cannot see through an HTTP(S) proxy (a loopback proxy is
# "local"), so also gate the HTTP clients by the *request host*. Transport-level,
# so ``httpx.MockTransport``-based tests are unaffected.
def _install_http_client_guards() -> None:
    try:
        import httpx

        real_handle = httpx.HTTPTransport.handle_request

        def handle_request(self, request):
            msg = _assert_host_allowed(request.url.host)
            if msg:
                raise httpx.ConnectError(msg, request=request)
            return real_handle(self, request)

        httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign]

        real_ahandle = httpx.AsyncHTTPTransport.handle_async_request

        async def handle_async_request(self, request):
            msg = _assert_host_allowed(request.url.host)
            if msg:
                raise httpx.ConnectError(msg, request=request)
            return await real_ahandle(self, request)

        httpx.AsyncHTTPTransport.handle_async_request = handle_async_request  # type: ignore[method-assign]
    except ImportError:
        pass
    try:
        import requests
        from urllib.parse import urlparse

        real_send = requests.adapters.HTTPAdapter.send

        def send(self, request, *args, **kwargs):
            msg = _assert_host_allowed(urlparse(request.url).hostname)
            if msg:
                raise requests.exceptions.ConnectionError(msg, request=request)
            return real_send(self, request, *args, **kwargs)

        requests.adapters.HTTPAdapter.send = send  # type: ignore[method-assign]
    except ImportError:
        pass
    try:
        import urllib.error
        import urllib.request
        from urllib.parse import urlparse

        real_open = urllib.request.OpenerDirector.open

        def opener_open(self, fullurl, *args, **kwargs):
            url = fullurl if isinstance(fullurl, str) else getattr(fullurl, "full_url", "")
            msg = _assert_host_allowed(urlparse(url).hostname)
            if msg:
                raise urllib.error.URLError(msg)
            return real_open(self, fullurl, *args, **kwargs)

        urllib.request.OpenerDirector.open = opener_open  # type: ignore[method-assign]
    except ImportError:
        pass


_install_http_client_guards()


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

