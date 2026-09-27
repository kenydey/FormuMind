"""W1-8 SearchDedupe 会话缓存（P0-13）测试。

全离线：用 monkeypatch 替换 _build_streams 模拟外部检索 stream，
统计 fetch 调用次数验证缓存行为。
"""
import time

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import literature


def _ev(i, identifier, title, relevance=0.9):
    return Evidence(
        source="openalex",
        identifier=identifier,
        title=title,
        snippet=f"snippet about {title}",
        relevance=relevance,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()
    yield
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()


def _mock_streams(monkeypatch, pages):
    calls = []

    def fake_fetch(off):
        calls.append(off)
        idx = 0 if off == 0 else 1
        return list(pages[idx]) if idx < len(pages) else []

    def fake_build_streams(*a, **k):
        return [{"name": "mock", "fetch": fake_fetch, "cursor": 0, "paged": True, "done": False}]

    monkeypatch.setattr(literature, "_build_streams", fake_build_streams)
    return calls


_PAGES = [
    [_ev(1, "10.1016/j.porgcoat.2019.105338", "epoxy coating zinc phosphate corrosion")],
    [],
]


def test_cache_hit_avoids_second_fetch(monkeypatch):
    """同 query 第二次调用走缓存：fetch 只发生一次，结果一致。"""
    calls = _mock_streams(monkeypatch, _PAGES)
    kw = dict(source_types=["literature"], req=None, total_limit=50, per_source_cap=50, max_rounds=5)
    final1, payload1 = literature.iter_search("epoxy coating zinc phosphate", **kw)
    n_calls_first = len(calls)
    assert n_calls_first > 0, "首次调用应触发 fetch"
    final2, payload2 = literature.iter_search("epoxy coating zinc phosphate", **kw)
    assert len(calls) == n_calls_first, "命中缓存后不应再次 fetch"
    assert [e.identifier for e in final2] == [e.identifier for e in final1]


def test_cache_hit_still_emits_final_progress(monkeypatch):
    """缓存命中时仍调用一次 progress_cb（终态），保持前端行为。"""
    _mock_streams(monkeypatch, _PAGES)
    kw = dict(source_types=["literature"], req=None, total_limit=50, per_source_cap=50, max_rounds=5)
    literature.iter_search("epoxy coating zinc phosphate", **kw)

    ticks = []
    metas = []
    literature.iter_search(
        "epoxy coating zinc phosphate",
        progress_cb=lambda partial, meta=None: (ticks.append(len(partial)), metas.append(meta)),
        **kw,
    )
    assert len(ticks) == 1, f"命中时应只触发一次终态回调，实际 {len(ticks)} 次"
    assert metas[0] is not None and metas[0].get("final") is True


def test_cache_expired_refetches(monkeypatch):
    """TTL 过期 → 重新检索。"""
    calls = _mock_streams(monkeypatch, _PAGES)
    kw = dict(source_types=["literature"], req=None, total_limit=50, per_source_cap=50, max_rounds=5)
    literature.iter_search("epoxy coating zinc phosphate", **kw)
    first = len(calls)
    # 手工把缓存条目时间戳改旧，模拟过期。
    with literature._SEARCH_CACHE_LOCK:
        for k, (ts, final, payload) in list(literature._SEARCH_CACHE.items()):
            literature._SEARCH_CACHE[k] = (time.monotonic() - 10_000, final, payload)
    literature.iter_search("epoxy coating zinc phosphate", **kw)
    assert len(calls) > first, "缓存过期后应重新 fetch"


def test_cache_isolated_by_query_and_limit(monkeypatch):
    """不同 query / 不同 limit 互不干扰（各自 fetch）。"""
    calls = _mock_streams(monkeypatch, _PAGES)
    base = dict(source_types=["literature"], req=None, per_source_cap=50, max_rounds=5)
    literature.iter_search("epoxy coating zinc phosphate", total_limit=50, **base)
    n1 = len(calls)
    literature.iter_search("cerium conversion coating", total_limit=50, **base)
    assert len(calls) > n1, "不同 query 应独立检索"
    n2 = len(calls)
    literature.iter_search("epoxy coating zinc phosphate", total_limit=25, **base)
    assert len(calls) > n2, "不同 limit 应独立检索"
    n3 = len(calls)
    # 同 query+同 limit 再次调用 → 命中
    literature.iter_search("epoxy coating zinc phosphate", total_limit=50, **base)
    assert len(calls) == n3


def test_cache_disabled_when_ttl_zero(monkeypatch):
    """search_cache_ttl_s=0 → 不读写缓存。"""
    calls = _mock_streams(monkeypatch, _PAGES)
    settings = get_settings()
    monkeypatch.setattr(settings, "search_cache_ttl_s", 0)
    kw = dict(source_types=["literature"], req=None, total_limit=50, per_source_cap=50, max_rounds=5)
    literature.iter_search("epoxy coating zinc phosphate", **kw)
    n1 = len(calls)
    literature.iter_search("epoxy coating zinc phosphate", **kw)
    assert len(calls) > n1, "TTL=0 时不应命中缓存"
    with literature._SEARCH_CACHE_LOCK:
        assert not literature._SEARCH_CACHE, "TTL=0 时不应写入缓存"


def test_cache_evicts_oldest_beyond_capacity():
    """容量 128，超限淘汰时间戳最旧的条目。"""
    now = time.monotonic()
    with literature._SEARCH_CACHE_LOCK:
        for i in range(128):
            literature._SEARCH_CACHE[f"k{i}"] = (now - (200 - i), [], {})
    literature._search_cache_put("k_new", [], {})
    with literature._SEARCH_CACHE_LOCK:
        assert len(literature._SEARCH_CACHE) == 128
        assert "k0" not in literature._SEARCH_CACHE, "最旧条目应被淘汰"
        assert "k_new" in literature._SEARCH_CACHE
