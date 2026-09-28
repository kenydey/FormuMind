"""Regression tests for the shared chem primitives (dedup 2026-09-28).

``cache_get``/``cache_put``/``_match_catalog`` in ``app.services.chem_common``
are the extractions of the former per-module copies (chemical_lookup /
external_alternatives / surechembl_client, and external_alternatives /
literature_alternatives). Each service keeps its own module-level ``_CACHE``
and ``_TTL_SEC`` and delegates via thin wrappers; these tests pin the shared
helpers' semantics (hit / miss / expiry eviction / catalog matching).
"""

import time

from app.services.chem_common import _match_catalog, cache_get, cache_put


def test_cache_put_get_roundtrip():
    store: dict = {}
    payload = {"rows": [1, 2, 3]}
    assert cache_put(store, "k", payload) is payload
    assert cache_get(store, "k", 60.0) is payload


def test_cache_get_miss_returns_none():
    assert cache_get({}, "nope", 60.0) is None


def test_cache_get_expired_evicts():
    store: dict = {}
    cache_put(store, "k", "v")
    store["k"] = (time.time() - 120.0, "v")  # age the entry past TTL
    assert cache_get(store, "k", 60.0) is None
    assert "k" not in store  # expired entry evicted


def test_match_catalog_by_name():
    from app.domain.knowledge import RAW_MATERIALS

    name = next(iter(RAW_MATERIALS))
    ok, matched = _match_catalog("", "", name)
    assert ok and matched == name


def test_match_catalog_no_match():
    ok, matched = _match_catalog("", "", "definitely-not-a-real-material-xyz")
    assert ok is False and matched is None


def test_service_wrappers_delegate_to_shared():
    """The per-service thin wrappers keep resolving module-level _CACHE."""
    from app.services import chemical_lookup, external_alternatives, surechembl_client

    for mod in (chemical_lookup, external_alternatives, surechembl_client):
        mod._cache_put("dedup-probe", {"x": 1})
        assert mod._cache_get("dedup-probe") == {"x": 1}
        assert "dedup-probe" in mod._CACHE
        mod._CACHE.pop("dedup-probe")
