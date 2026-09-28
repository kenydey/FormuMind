"""B-14 回归：citation_date_guard 的域名白名单必须做边界匹配，
此前子串匹配使白名单 ``example.com`` 误命中 ``evil-example.com``。"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.citation_date_guard import filter_evidence_by_domain


def _ev(url=None, source=""):
    return SimpleNamespace(url=url, url_alt=None, source=source)


def test_substring_domain_no_longer_matches():
    # 核心回归：evil-example.com 不得被 example.com 白名单放行
    evil = _ev(url="https://evil-example.com/malware")
    good = _ev(url="https://example.com/paper")
    out = filter_evidence_by_domain([evil, good], ["example.com"])
    assert out == [good]


def test_suffix_attack_domain_blocked():
    # example.com.evil.com 同样不是白名单域名
    sneaky = _ev(url="https://example.com.evil.com/x")
    out = filter_evidence_by_domain([sneaky], ["example.com"])
    assert out == []


def test_exact_and_subdomain_still_allowed():
    # 精确匹配与合法子域名保持放行
    exact = _ev(url="https://example.com/a")
    sub = _ev(url="https://sub.example.com/b")
    deep = _ev(url="https://a.b.example.com/c")
    out = filter_evidence_by_domain([exact, sub, deep], ["example.com"])
    assert out == [exact, sub, deep]


def test_host_match_case_insensitive():
    ev = _ev(url="https://EXAMPLE.com/x")
    assert filter_evidence_by_domain([ev], ["example.com"]) == [ev]


def test_source_fallback_still_substring():
    # URL 缺失时回退匹配 source 名称子串（如 openalex），行为不变
    ev = _ev(source="OpenAlex")
    assert filter_evidence_by_domain([ev], ["openalex"]) == [ev]
    assert filter_evidence_by_domain([ev], ["example.com"]) == []


def test_empty_allowlist_passthrough():
    evs = [_ev(url="https://anything.io/x")]
    assert filter_evidence_by_domain(evs, None) == evs
    assert filter_evidence_by_domain(evs, []) == evs
