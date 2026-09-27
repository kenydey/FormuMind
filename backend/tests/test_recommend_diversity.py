"""W1-7 检索 MMR（P0-10）测试：select_diverse_mmr_text 单元测试 + iter_search 接入测试。

全离线：iter_search 用 monkeypatch 替换 _build_streams。
"""
import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import literature
from app.services.recommend_diversity import select_diverse_mmr_text


def _ev(identifier, title, snippet, relevance=0.9):
    return Evidence(
        source="openalex",
        identifier=identifier,
        title=title,
        snippet=snippet,
        relevance=relevance,
    )


_A_TITLE = "waterborne epoxy anticorrosive coating zinc phosphate primer steel protection"
_A_SNIPPET = "two component waterborne epoxy primer zinc phosphate salt spray steel"
_AP_TITLE = "waterborne epoxy anticorrosive coating zinc phosphate primer formulation application"
_AP_SNIPPET = "waterborne epoxy coating zinc phosphate primer steel corrosion protection"
_B_TITLE = "cerium conversion coating aluminum alloy passivation treatment"
_B_SNIPPET = "cerium nitrate post treatment aluminum alloy corrosion inhibition"


def _trio():
    return [
        _ev("doi:a", _A_TITLE, _A_SNIPPET),
        _ev("doi:ap", _AP_TITLE, _AP_SNIPPET),
        _ev("doi:b", _B_TITLE, _B_SNIPPET),
    ]


# ── 单元测试 ──────────────────────────────────────────────────────────────────


def test_mmr_spreads_near_duplicates():
    """近重复（A/A'）被分散：n=2 时应选中 A 与互异的 B，而非 A'。"""
    out = select_diverse_mmr_text(_trio(), 2, lambda_score=0.5)
    assert [e.identifier for e in out] == ["doi:a", "doi:b"]


def test_mmr_lambda_one_degenerates_to_input_order():
    """λ=1.0 时多样性项权重为 0，退化为原序返回（n<len 与全量两种情形）。"""
    items = _trio()
    out = select_diverse_mmr_text(items, 2, lambda_score=1.0)
    assert [e.identifier for e in out] == ["doi:a", "doi:ap"]
    out_all = select_diverse_mmr_text(items, 10, lambda_score=1.0)
    assert [e.identifier for e in out_all] == ["doi:a", "doi:ap", "doi:b"]


def test_mmr_n_ge_len_full_rerank_permutation():
    """n≥len 时做全量 MMR 重排：返回原集合的多样性排序，不丢条目。"""
    items = _trio()
    out = select_diverse_mmr_text(items, 10, lambda_score=0.5)
    assert sorted(e.identifier for e in out) == ["doi:a", "doi:ap", "doi:b"]
    assert [e.identifier for e in out] == ["doi:a", "doi:b", "doi:ap"]
    assert [e.identifier for e in items] == ["doi:a", "doi:ap", "doi:b"], "输入不应被修改"


def test_mmr_empty_and_zero():
    assert select_diverse_mmr_text([], 3) == []
    assert select_diverse_mmr_text(_trio(), 0) == []


# ── iter_search 接入测试 ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_cache():
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()
    yield
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()


def _mock_streams(monkeypatch):
    items = _trio()

    def fake_fetch(off):
        return list(items) if off == 0 else []

    def fake_build_streams(*a, **k):
        return [{"name": "mock", "fetch": fake_fetch, "cursor": 0, "paged": True, "done": False}]

    monkeypatch.setattr(literature, "_build_streams", fake_build_streams)


def _run(query="epoxy coating zinc phosphate cerium"):
    return literature.iter_search(
        query,
        ["literature"],
        req=None,
        total_limit=10,
        per_source_cap=50,
        max_rounds=5,
    )


def test_iter_search_mmr_flag_off_by_default(monkeypatch):
    """默认关闭：payload 标记 mmr_applied=False，主流程不受影响。"""
    _mock_streams(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "search_mmr_enabled", False)
    final, payload = _run()
    assert payload.get("mmr_applied") is False
    assert len(final) == 3


def test_iter_search_mmr_reorders_for_diversity(monkeypatch):
    """开启后：互异的 B 被提前到第二位，近重复 A' 被推后。"""
    _mock_streams(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "search_mmr_enabled", True)
    monkeypatch.setattr(settings, "search_mmr_lambda", 0.5)
    final, payload = _run()
    assert payload.get("mmr_applied") is True
    ids = [e.identifier for e in final]
    assert ids[0] == "doi:a"
    assert ids[1] == "doi:b", f"期望互异条目排第二，实际 {ids}"
