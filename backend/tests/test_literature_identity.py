"""W1-4 文献去重（P0-6）测试。

全离线：归一化/身份键/dedupe 纯单元测试；iter_search 集成用 monkeypatch
替换 _build_streams，不触网。
"""
import pytest

from app.domain.schemas import Evidence
from app.services import literature
from app.services.literature_identity import (
    dedupe_items,
    identity_keys,
    normalize_arxiv,
    normalize_doi,
    normalize_title,
)


# ── normalize_doi ─────────────────────────────────────────────────────────────


def test_normalize_doi_case_and_prefix():
    assert normalize_doi("10.1016/J.PORGCOAT.2019.105338") == "10.1016/j.porgcoat.2019.105338"
    assert normalize_doi("https://doi.org/10.1016/J.PORGCOAT.2019.105338") == "10.1016/j.porgcoat.2019.105338"
    assert normalize_doi("http://dx.doi.org/10.1016/j.porgcoat.2019.105338") == "10.1016/j.porgcoat.2019.105338"
    assert normalize_doi("doi:10.1016/j.porgcoat.2019.105338") == "10.1016/j.porgcoat.2019.105338"
    assert normalize_doi("  10.1016/j.porgcoat.2019.105338  ") == "10.1016/j.porgcoat.2019.105338"
    assert normalize_doi("") == ""
    assert normalize_doi(None) == ""


def test_normalize_arxiv_version_stripped():
    assert normalize_arxiv("arXiv:2101.00001v2") == "2101.00001"
    assert normalize_arxiv("2101.00001V3") == "2101.00001"
    assert normalize_arxiv("2101.00001", strip_version=False) == "2101.00001"
    assert normalize_arxiv("") == ""


def test_normalize_title_punctuation_and_case():
    a = normalize_title("Waterborne epoxy anticorrosive coating: zinc phosphate!")
    b = normalize_title("waterborne  epoxy   anticorrosive coating, zinc phosphate")
    assert a == b and a != ""


# ── identity_keys ─────────────────────────────────────────────────────────────


def test_identity_keys_composite_needs_all_three():
    full = {"title": "MBT-doped epoxy coatings", "year": "2019", "authors": ["Zhang, Wei"]}
    keys = identity_keys(full)
    assert any(k.startswith("tiyr:") for k in keys)

    no_year = {"title": "MBT-doped epoxy coatings", "authors": ["Zhang, Wei"]}
    assert not any(k.startswith("tiyr:") for k in identity_keys(no_year))

    no_author = {"title": "MBT-doped epoxy coatings", "year": "2019"}
    assert not any(k.startswith("tiyr:") for k in identity_keys(no_author))

    no_title = {"year": "2019", "authors": ["Zhang, Wei"]}
    assert not any(k.startswith("tiyr:") for k in identity_keys(no_title))


def test_identity_keys_doi_arxiv():
    keys = identity_keys({"doi": "https://doi.org/10.1/X", "arxiv": "arXiv:2101.1v2"})
    assert "doi:10.1/x" in keys
    assert "arxiv:2101.1" in keys


# ── dedupe_items ──────────────────────────────────────────────────────────────


def _item(i, **kw):
    d = {"id": f"it{i}", **kw}
    return d


def test_dedupe_merges_same_doi_variants():
    items = [
        _item(1, doi="10.1016/j.porgcoat.2019.105338", title="MBT epoxy"),
        _item(2, doi="https://doi.org/10.1016/J.PORGCOAT.2019.105338", title="MBT epoxy coatings"),
        _item(3, doi="10.1016/j.surfcoat.2017.06.001", title="Cerium passivation"),
    ]
    out, merged = dedupe_items(items)
    assert len(out) == 2
    assert merged == {"it2": "it1"}


def test_dedupe_refuses_merge_on_conflicting_doi():
    # 同 scheme（doi）出现不一致精确值 → 拒绝合并（保守策略）。
    items = [
        _item(1, doi="10.1/aaa", title="Same title", year="2020", authors=["Li"]),
        _item(2, doi="10.1/bbb", title="Same title", year="2020", authors=["Li"]),
    ]
    out, merged = dedupe_items(items)
    assert len(out) == 2
    assert merged == {}


def test_dedupe_merges_on_composite_key():
    items = [
        _item(1, title="Chrome-free conversion coating: aluminum!", year="2020", authors=["Wang, Tao"]),
        _item(2, title="chrome free conversion coating aluminum", year="2020", authors=["Wang, Tao"]),
    ]
    out, merged = dedupe_items(items)
    assert len(out) == 1
    assert merged == {"it2": "it1"}


def test_dedupe_transitive_merge():
    # a↔b 共享 doi，b↔c 共享复合键 → 三者同组。
    items = [
        _item(1, doi="10.1/x", title="T1", year="2020", authors=["A"]),
        _item(2, doi="https://doi.org/10.1/X", title="T1", year="2020", authors=["A"]),
        _item(3, title="T1", year="2020", authors=["A"]),
    ]
    out, merged = dedupe_items(items)
    assert len(out) == 1
    assert merged == {"it2": "it1", "it3": "it1"}


def test_dedupe_empty():
    assert dedupe_items([]) == ([], {})


# ── iter_search 集成：轮次间身份键去重 ─────────────────────────────────────────


def _ev(i, identifier, title, relevance=0.9):
    return Evidence(
        source="openalex",
        identifier=identifier,
        title=title,
        snippet=f"snippet about {title}",
        relevance=relevance,
    )


def test_iter_search_dedupes_doi_variants_across_pages(monkeypatch):
    """同一 DOI 以不同写法（前缀/大小写）在两轮出现 → 只保留一条。"""
    pages = [
        [
            _ev(1, "10.1016/j.porgcoat.2019.105338", "epoxy coating zinc phosphate corrosion"),
            _ev(2, "10.1016/j.surfcoat.2017.06.001", "cerium conversion coating aluminum alloy"),
        ],
        [
            _ev(3, "https://doi.org/10.1016/J.PORGCOAT.2019.105338", "epoxy coating zinc phosphate corrosion protection"),
        ],
    ]
    calls = []

    def fake_fetch(off):
        calls.append(off)
        return list(pages[0] if off == 0 else pages[1]) if off <= 50 else []

    def fake_build_streams(*a, **k):
        return [{"name": "mock", "fetch": fake_fetch, "cursor": 0, "paged": True, "done": False}]

    monkeypatch.setattr(literature, "_build_streams", fake_build_streams)
    final, _ = literature.iter_search(
        "epoxy coating zinc phosphate",
        ["literature"],
        req=None,
        total_limit=50,
        per_source_cap=50,
        max_rounds=5,
    )
    doi_hits = [e for e in final if "10.1016/j.porgcoat.2019.105338" in e.identifier.lower().replace("https://doi.org/", "")]
    assert len(doi_hits) == 1, [e.identifier for e in final]
    assert len(final) == 2
    assert calls, "stream fetch 应被调用"
