"""Search-result quality filter — drops junk before it reaches the KB.

Two tiers, mounted at the single retrieval convergence point
(``literature._merge_filter_rank``):

1. **Rule tier** (pure Python, runs on every merge round):
   * blocked-domain list (shopping / social / SEO farms);
   * garbage snippets (too short, or mostly non-text symbols);
   * SimHash near-duplicate collapse (catches the same article syndicated
     across mirrors, which identifier-level dedup cannot).
2. **LLM tier** (optional, one batched call at the end of a search):
   judges the final ranked list and drops items an expert would consider
   irrelevant.  Off by default; degrades to keep-all on any failure.

Seed-corpus evidence is never touched — offline behaviour stays identical.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..config import get_settings
from ..domain.schemas import Evidence
from .errors import degrade_return

logger = logging.getLogger(__name__)

# Conservative default blocklist: platforms that essentially never contain
# citable formulation prior art. Extendable via FORMUMIND_CONTENT_FILTER_BLOCKED_DOMAINS.
DEFAULT_BLOCKED_DOMAINS = (
    "pinterest.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "amazon.com",
    "ebay.com",
    "aliexpress.com",
    "alibaba.com",
    "made-in-china.com",
    "taobao.com",
    "tmall.com",
    "temu.com",
)

_WORD_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


@dataclass
class FilterReport:
    """Aggregated outcome of one filter pass (for logs / SSE / UI)."""

    kept: int = 0
    dropped: int = 0
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    dropped_examples: list[str] = field(default_factory=list)

    def record_drop(self, reason: str, ev: Evidence) -> None:
        self.dropped += 1
        self.dropped_by_reason[reason] = self.dropped_by_reason.get(reason, 0) + 1
        if len(self.dropped_examples) < 8:
            self.dropped_examples.append(f"[{reason}] {ev.title[:60]}")

    def merge(self, other: "FilterReport") -> None:
        """Combine drop stats from another pass (rule tier + LLM judge)."""
        self.dropped += other.dropped
        for reason, count in other.dropped_by_reason.items():
            self.dropped_by_reason[reason] = self.dropped_by_reason.get(reason, 0) + count
        for example in other.dropped_examples:
            if len(self.dropped_examples) >= 8:
                break
            self.dropped_examples.append(example)

    def as_dict(self) -> dict:
        return {
            "kept": self.kept,
            "dropped": self.dropped,
            "dropped_by_reason": dict(self.dropped_by_reason),
            "dropped_examples": list(self.dropped_examples),
        }


def _blocked_domains() -> tuple[str, ...]:
    extra = get_settings().content_filter_blocked_domains
    return DEFAULT_BLOCKED_DOMAINS + tuple(d.strip().lower() for d in extra if d.strip())


def _domain_of(ev: Evidence) -> str:
    ident = (ev.identifier or "").strip()
    if not ident.lower().startswith(("http://", "https://")):
        return ""
    try:
        host = urlparse(ident).hostname or ""
    except ValueError:
        return ""
    return host.lower().lstrip("www.")


def _is_blocked_domain(ev: Evidence) -> bool:
    host = _domain_of(ev)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in _blocked_domains())


def _is_garbage_snippet(ev: Evidence, min_chars: int) -> bool:
    text = f"{ev.title} {ev.snippet}".strip()
    if len(text) < min_chars:
        return True
    # Mostly symbols / markup debris → garbage.
    word_chars = len(_WORD_RE.findall(text))
    return word_chars / max(1, len(text)) < 0.4


# ── SimHash near-duplicate detection ─────────────────────────────────────────
# Character-3-gram shingles work for both English and CJK without tokenizers.

_SIMHASH_BITS = 64


def _shingle_hash(shingle: str) -> int:
    """Deterministic 64-bit shingle hash (Python's hash() is salted per process)."""
    import hashlib

    return int.from_bytes(hashlib.md5(shingle.encode("utf-8")).digest()[:8], "big")


def simhash(text: str) -> int:
    text = re.sub(r"\s+", " ", text.lower()).strip()
    if len(text) < 3:
        return 0
    weights = [0] * _SIMHASH_BITS
    for i in range(len(text) - 2):
        h = _shingle_hash(text[i : i + 3])
        for bit in range(_SIMHASH_BITS):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    out = 0
    for bit, w in enumerate(weights):
        if w > 0:
            out |= 1 << bit
    return out


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ≤8 differing bits ≈ >87% shingle overlap — syndicated copies with minor
# suffixes collapse, while genuinely distinct passages (~32 bits apart) survive.
_NEAR_DUP_MAX_DISTANCE = 8

_WEBLIKE_SOURCES = frozenset(
    {"internet", "web", "tavily", "serpapi", "duckduckgo", "chemcrow-web", "google"}
)


def _is_weblike(ev: Evidence) -> bool:
    """Near-dup collapse targets web sources only: mirrors/SEO copies are a web
    phenomenon, while patents & literature carry authoritative unique IDs
    (patent family members legitimately share abstracts)."""
    src = (ev.source or "").lower()
    if any(w in src for w in _WEBLIKE_SOURCES):
        return True
    return (ev.identifier or "").lower().startswith(("http://", "https://"))


def filter_evidence(
    evidence: list[Evidence], query: str = ""
) -> tuple[list[Evidence], FilterReport]:
    """Rule-tier filter. Returns (kept, report).

    Input order is preserved (callers pass rank-sorted lists, so near-dup
    collapse keeps the higher-ranked copy).  Seed-corpus rows pass through
    untouched.  No-op when ``content_filter_enabled`` is false.
    """
    report = FilterReport()
    settings = get_settings()
    if not settings.content_filter_enabled:
        report.kept = len(evidence)
        return evidence, report

    min_chars = settings.content_filter_min_snippet_chars
    kept: list[Evidence] = []
    seen_hashes: list[int] = []
    for ev in evidence:
        if ev.is_seed_corpus:
            kept.append(ev)
            continue
        if _is_blocked_domain(ev):
            report.record_drop("blocked_domain", ev)
            continue
        if _is_garbage_snippet(ev, min_chars):
            report.record_drop("garbage_snippet", ev)
            continue
        if _is_weblike(ev):
            sh = simhash(f"{ev.title} {ev.snippet}")
            if sh and any(
                _hamming(sh, prev) <= _NEAR_DUP_MAX_DISTANCE for prev in seen_hashes
            ):
                report.record_drop("near_duplicate", ev)
                continue
            if sh:
                seen_hashes.append(sh)
        kept.append(ev)

    report.kept = len(kept)
    if report.dropped:
        logger.info(
            "content_filter: kept %d dropped %d (%s)",
            report.kept,
            report.dropped,
            report.dropped_by_reason,
        )
    return kept, report


# ── LLM batch quality judge (final pass, optional) ───────────────────────────

_JUDGE_PROMPT = """你是检索质量审查员。给定研究主题与候选检索结果，判断每条是否值得进入研发知识库。
丢弃标准：与主题无关、纯商品售卖页、目录/聚合页、内容空洞无技术信息。
保留标准：专利、论文、技术文章、包含配方/工艺/性能数据的页面（宁可保留，不确定时保留）。

研究主题：{query}

候选结果：
{items}

仅返回 JSON：{{"drop": [编号, ...]}}（要丢弃的方括号编号列表；全部保留则返回空列表）。"""


def llm_quality_judge(evidence: list[Evidence], query: str) -> tuple[list[Evidence], FilterReport]:
    """One batched LLM call that drops clearly-irrelevant final results.

    Gated by ``content_filter_llm_judge``; keeps everything on any failure,
    when no LLM key is configured, and never touches seed-corpus rows.
    """
    report = FilterReport(kept=len(evidence))
    settings = get_settings()
    if (
        not settings.content_filter_llm_judge
        or not settings.content_filter_enabled
        or len(evidence) < 2
        or not settings.get_active_api_key()
    ):
        return evidence, report

    from . import llm

    items = "\n".join(
        f"[{i}] ({ev.source}) {ev.title}: {ev.snippet[:150]}"
        for i, ev in enumerate(evidence)
    )
    try:
        data = llm.complete_json(_JUDGE_PROMPT.format(query=query, items=items))
    except Exception as exc:
        return degrade_return(logger, exc, "llm quality judge failed", (evidence, report))

    drops = (data or {}).get("drop") if isinstance(data, dict) else None
    if not isinstance(drops, list):
        return evidence, report
    drop_idx = set()
    for d in drops:
        try:
            drop_idx.add(int(d))
        except (TypeError, ValueError):
            continue
    # Safety valve: an LLM asking to drop most of the list is itself suspect.
    if len(drop_idx) > len(evidence) // 2:
        logger.warning("llm quality judge tried to drop %d/%d — ignored", len(drop_idx), len(evidence))
        return evidence, report

    kept: list[Evidence] = []
    report = FilterReport()
    for i, ev in enumerate(evidence):
        if i in drop_idx and not ev.is_seed_corpus:
            report.record_drop("llm_judge", ev)
        else:
            kept.append(ev)
    report.kept = len(kept)
    return kept, report
