"""Where the knowledge-base build actually spends its time.

The ingest pipeline had no instrumentation at all, so every question about it
was answered by guessing — and guessing was wrong twice in a row. The patent
tier looked like a parser problem and turned out to be three dead endpoints
burning timeouts; the arXiv tier looked like a parser problem and turned out
to be OCR firing on figure pages. Both were found by measuring directly, and
this module is that measurement made permanent.

Shape of the output — one line per document, one per batch::

    kb_ingest doc US9982145B2 [patent] indexed 1180ms
      download=703 parse=41(html) chunk=12 entities=390 embed=34 chars=18392
    kb_ingest batch 340 docs in 412.8s
      indexed=291 failed=44 skipped=5 | download=61% parse=8% embed=22%
      web_chars p50=4180 p90=21903 thin(<200)=12%

Two details that make it usable rather than decorative:

* **A document is timed across threads.** Fetching runs in a worker pool and
  indexing runs serially on the main thread, so a plain ``threading.local``
  would split each document into two unrelated halves. The record lives in a
  shared registry keyed by identifier; the thread-local only tracks which
  document the *current* thread is working on, so deeply nested code
  (``parse_document``) can attribute its time without being passed a handle.
* **The web channel reports its output size.** ``_fetch_web_text`` only checks
  a 200-character floor, so a JavaScript shell and a genuinely short page are
  both just ``failed`` — indistinguishable. The character distribution is what
  decides whether a dynamic-rendering tier is worth its dependencies.

Never raises and never changes behaviour: every entry point degrades to a
no-op if anything is off, because a metrics bug must not fail an ingest.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Which document the calling thread is currently working on.
_current = threading.local()

# identifier -> record. Shared because a document's fetch and index phases run
# on different threads.
_records: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

_batch: dict[str, Any] | None = None


def _rec() -> dict[str, Any] | None:
    ident = getattr(_current, "ident", None)
    if not ident:
        return None
    with _lock:
        return _records.get(ident)


@contextmanager
def track(identifier: str, kind: str = "") -> Iterator[None]:
    """Mark this thread as working on *identifier* for the duration.

    Re-entrant across phases: the second call (indexing) picks the existing
    record back up rather than starting a new one, so one line covers the
    document's whole life.
    """
    if not identifier:
        yield
        return
    with _lock:
        rec = _records.get(identifier)
        if rec is None:
            rec = {"identifier": identifier, "kind": kind, "spans": Counter(), "notes": {}}
            _records[identifier] = rec
        elif kind:
            rec["kind"] = kind
    prev = getattr(_current, "ident", None)
    _current.ident = identifier
    try:
        yield
    finally:
        _current.ident = prev


@contextmanager
def span(name: str) -> Iterator[None]:
    """Accumulate wall time under *name* for the current document."""
    rec = _rec()
    if rec is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - start) * 1000.0
        with _lock:
            rec["spans"][name] += elapsed


def note(**facts: Any) -> None:
    """Record a non-timing fact (parser tier, extracted length, …)."""
    rec = _rec()
    if rec is None:
        return
    with _lock:
        rec["notes"].update(facts)


def finish(identifier: str, status: str) -> None:
    """Emit the per-document line and fold the record into the batch totals."""
    if not identifier:
        return
    with _lock:
        rec = _records.pop(identifier, None)
    if rec is None:
        return
    spans = rec["spans"]
    notes = rec["notes"]

    # `fetch` wraps the whole acquisition, parsing included, so the interesting
    # number — time actually spent on the wire — is what is left over.
    download = max(0.0, spans.get("fetch", 0.0) - spans.get("parse", 0.0))
    parts = [f"download={download:.0f}"]
    parser = notes.get("parser")
    for name in ("parse", "chunk", "entities", "embed"):
        value = spans.get(name)
        if value:
            label = f"{name}={value:.0f}"
            if name == "parse" and parser:
                # `hybrid+mineru:3` rather than a bare `hybrid`: MinerU costs
                # quota per page, so which documents actually used it is the
                # thing worth being able to read off a batch log.
                pages = notes.get("mineru_pages")
                label += f"({parser}+mineru:{pages})" if pages else f"({parser})"
            parts.append(label)
    if notes.get("chars") is not None:
        parts.append(f"chars={notes['chars']}")
    total = download + sum(spans.get(n, 0.0) for n in ("parse", "chunk", "entities", "embed"))
    logger.info(
        "kb_ingest doc %s [%s] %s %.0fms  %s",
        rec["identifier"], rec["kind"] or "?", status, total, " ".join(parts),
    )

    global _batch
    if _batch is None:
        return
    with _lock:
        _batch["status"][status] += 1
        _batch["kind"][rec["kind"] or "?"] += 1
        _batch["spans"]["download"] += download
        for name in ("parse", "chunk", "entities", "embed"):
            _batch["spans"][name] += spans.get(name, 0.0)
        if rec["kind"] == "web":
            http = notes.get("http")
            if http is not None and http != 200:
                # An HTTP refusal is not thin content. Mixing them would let a
                # wall of 403s argue for a JavaScript-rendering tier that
                # cannot fix a 403.
                _batch["web_http_errors"][http] += 1
            elif notes.get("chars") is not None:
                _batch["web_chars"].append(int(notes["chars"]))


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return ordered[idx]


@contextmanager
def batch(label: str = "kb_ingest") -> Iterator[None]:
    """Aggregate every document timed inside this block into one summary."""
    global _batch
    prev = _batch
    _batch = {
        "label": label,
        "started": time.perf_counter(),
        "status": Counter(),
        "kind": Counter(),
        "spans": Counter(),
        "web_chars": [],
        "web_http_errors": Counter(),
    }
    try:
        yield
    finally:
        current, _batch = _batch, prev
        try:
            _log_batch(current)
        except Exception as exc:  # a metrics bug must not fail an ingest
            logger.debug("ingest_timing batch summary failed: %s", exc)


def _log_batch(b: dict[str, Any]) -> None:
    elapsed = time.perf_counter() - b["started"]
    docs = sum(b["status"].values())
    if not docs:
        return
    spans = b["spans"]
    total = sum(spans.values()) or 1.0
    shares = " ".join(f"{name}={spans[name] / total * 100:.0f}%" for name in
                      ("download", "parse", "chunk", "entities", "embed") if spans.get(name))
    status = " ".join(f"{k}={v}" for k, v in sorted(b["status"].items()))
    logger.info("%s batch %d docs in %.1fs  %s | %s", b["label"], docs, elapsed, status, shares)

    chars = b["web_chars"]
    errors = b["web_http_errors"]
    if chars or errors:
        # The deciding numbers for a dynamic-rendering tier. `thin` counts only
        # pages the server actually served: a JavaScript shell is thin content,
        # a 403 is a different problem and no renderer fixes it.
        parts = []
        if chars:
            thin = sum(1 for c in chars if c < 200) / len(chars) * 100
            parts.append(
                f"n={len(chars)} p50={_percentile(chars, 0.5)} "
                f"p90={_percentile(chars, 0.9)} thin(<200)={thin:.0f}%"
            )
        if errors:
            parts.append("http_errors=" + ",".join(f"{k}:{v}" for k, v in sorted(errors.items())))
        logger.info("%s batch web_chars %s", b["label"], " ".join(parts))


def reset() -> None:
    """Drop all state — for tests, and for a worker restarting mid-batch."""
    global _batch
    with _lock:
        _records.clear()
    _batch = None
    _current.ident = None
