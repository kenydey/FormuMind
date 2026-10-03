"""get_settings(): a build that raced a cache_clear() must not pin stale values.

``functools.lru_cache`` caches the *first* of two concurrent builds to finish.
A background thread (an eager task, the lifespan outbox-recovery thread, ...)
that started building before an environment change could therefore finish
first and pin the old object, while the caller that cleared the cache got a
correct one it could not keep — e.g. API auth silently off right after being
enabled. This was the cause of the intermittent ``tests/test_api_auth.py``
failures (a leftover ``eager-recommend-*`` thread racing the auth fixture).
"""
from __future__ import annotations

import threading

import pytest

from app import config


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_cache_returns_one_object_until_cleared():
    first = config.get_settings()
    assert config.get_settings() is first
    config.get_settings.cache_clear()
    second = config.get_settings()
    assert second is not first
    assert config.get_settings() is second


def test_build_that_raced_a_clear_cannot_pin_stale_settings(monkeypatch):
    """Deterministic replay of the failing interleaving.

    A stray thread builds with the OLD env and finishes between the main
    thread's cache_clear() and the end of the main thread's own (correct) build.
    """
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")

    real_audit = config._audit_formumind_env
    main_building = threading.Event()
    stray_done = threading.Event()
    calls: list[str] = []
    lock = threading.Lock()

    def gated_audit():
        with lock:
            calls.append(threading.current_thread().name)
            n = len(calls)
        if n == 1:
            # Stray thread: Settings already read the OLD env; hold here until
            # the main thread has started its own build, then finish first.
            assert main_building.wait(timeout=10)
        elif n == 2:
            # Main thread's build: only finish after the stray one is done.
            main_building.set()
            assert stray_done.wait(timeout=10)
        real_audit()

    monkeypatch.setattr(config, "_audit_formumind_env", gated_audit)

    stray_result: list = []

    def stray():
        stray_result.append(config.get_settings())
        stray_done.set()

    t = threading.Thread(target=stray, name="stray-build")
    t.start()
    # Wait until the stray thread is inside its (stale) build.
    for _ in range(1000):
        with lock:
            if calls:
                break
        threading.Event().wait(0.005)
    assert calls == ["stray-build"]

    # The "environment change": new value, then the cache is cleared.
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    config.get_settings.cache_clear()
    main_result = config.get_settings()
    t.join(timeout=10)

    assert main_result.api_auth_enabled is True
    # Whatever the stray thread returned, nothing stale is cached afterwards.
    assert config.get_settings().api_auth_enabled is True
    assert stray_result and stray_result[0].api_auth_enabled is True


def test_concurrent_readers_share_one_cached_object():
    results: list = []

    def reader():
        results.append(config.get_settings())

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)
    assert len(results) == 8
    assert all(r is config.get_settings() for r in results)


def test_failed_build_is_not_cached(monkeypatch):
    boom = {"on": True}
    real = config._build_settings

    def flaky():
        if boom["on"]:
            raise ValueError("bad env")
        return real()

    monkeypatch.setattr(config, "_build_settings", flaky)
    with pytest.raises(ValueError):
        config.get_settings()
    boom["on"] = False
    assert config.get_settings() is config.get_settings()
