"""Ingest instrumentation — cross-thread attribution, and never breaking ingest."""
from __future__ import annotations

import logging
import threading
import time

import pytest

from app.services import ingest_timing as timing


@pytest.fixture(autouse=True)
def _clean():
    timing.reset()
    yield
    timing.reset()


def _lines(caplog: pytest.LogCaptureFixture, needle: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


# ── per-document line ────────────────────────────────────────────────────────


def test_document_line_reports_each_phase(caplog):
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.batch():
        with timing.track("US123", "patent"):
            with timing.span("fetch"):
                with timing.span("parse"):
                    time.sleep(0.01)
            timing.note(parser="hybrid", chars=4096)
            with timing.span("embed"):
                time.sleep(0.005)
        timing.finish("US123", "indexed")

    line = _lines(caplog, "doc US123")[0]
    assert "[patent]" in line and "indexed" in line
    assert "parse=" in line and "(hybrid)" in line
    assert "embed=" in line
    assert "chars=4096" in line


def test_download_excludes_nested_parse_time(caplog):
    """`fetch` wraps parsing too, so time on the wire is the difference.

    Reporting them as siblings would double-count and make a slow parser look
    like a slow network — the exact misdiagnosis this instrumentation exists to
    prevent. Asserted on the emitted line, since that is what gets read.
    """
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.track("X1", "web"):
        with timing.span("fetch"):
            time.sleep(0.03)
            with timing.span("parse"):
                time.sleep(0.05)
    timing.finish("X1", "indexed")

    line = _lines(caplog, "doc X1")[0]
    download = float(line.split("download=")[1].split()[0])
    parse = float(line.split("parse=")[1].split("(")[0].split()[0])
    assert parse >= 45, line
    # fetch was ~80 ms of which ~50 ms was parse, so the wire share is ~30 ms.
    assert 10 < download < 50, line
    assert download < parse, "download must not absorb the nested parse time"


def test_finish_is_idempotent_and_safe_for_unknown_ids():
    timing.finish("never-tracked", "failed")  # must not raise
    with timing.track("A", "web"):
        pass
    timing.finish("A", "indexed")
    timing.finish("A", "indexed")  # second call: record already popped


def test_span_outside_any_document_is_a_noop():
    with timing.span("parse"):
        pass  # no active document — must not raise or leak state
    assert timing._records == {}


def test_note_outside_any_document_is_a_noop():
    timing.note(parser="hybrid")
    assert timing._records == {}


# ── cross-thread attribution ─────────────────────────────────────────────────


def test_document_survives_the_fetch_to_index_thread_handoff(caplog):
    """Fetching runs in a worker pool, indexing on the main thread.

    A plain threading.local would split each document into two unrelated
    halves and neither line would show the whole cost.
    """
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")

    def fetch_phase():
        with timing.track("EP42", "patent"):
            with timing.span("fetch"):
                time.sleep(0.01)

    worker = threading.Thread(target=fetch_phase)
    worker.start()
    worker.join()

    with timing.track("EP42", "patent"):  # index phase, main thread
        with timing.span("embed"):
            time.sleep(0.01)
    timing.finish("EP42", "indexed")

    line = _lines(caplog, "doc EP42")[0]
    # `download=` is printed unconditionally, so the substring proves nothing —
    # the claim is that the worker thread's fetch time reached this line.
    download = float(line.split("download=")[1].split()[0])
    assert download >= 5, f"fetch time was lost at the thread handoff: {line}"
    assert "embed=" in line


def test_concurrent_documents_do_not_mix_spans():
    """Each worker must attribute its time to its own document."""
    done = threading.Barrier(3)

    def work(ident: str, sleep_s: float):
        with timing.track(ident, "web"):
            with timing.span("fetch"):
                time.sleep(sleep_s)
        done.wait()

    a = threading.Thread(target=work, args=("A", 0.05))
    b = threading.Thread(target=work, args=("B", 0.005))
    a.start()
    b.start()
    done.wait()

    assert timing._records["A"]["spans"]["fetch"] > timing._records["B"]["spans"]["fetch"] * 3
    a.join()
    b.join()


def test_nested_track_restores_the_outer_document():
    with timing.track("OUTER", "patent"):
        with timing.track("INNER", "web"):
            with timing.span("fetch"):
                pass
        with timing.span("embed"):
            pass
    assert "embed" in timing._records["OUTER"]["spans"]
    assert "embed" not in timing._records["INNER"]["spans"]


# ── batch summary ────────────────────────────────────────────────────────────


def test_batch_summary_counts_statuses_and_shares(caplog):
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.batch("kb_ingest"):
        for ident, status in (("A", "indexed"), ("B", "indexed"), ("C", "failed")):
            with timing.track(ident, "patent"):
                with timing.span("fetch"):
                    time.sleep(0.005)
            timing.finish(ident, status)

    line = _lines(caplog, "batch 3 docs")[0]
    assert "indexed=2" in line and "failed=1" in line
    assert "download=" in line


def test_batch_reports_web_character_distribution(caplog):
    """The deciding evidence for a dynamic-rendering tier.

    `_fetch_web_text` only checks a 200-character floor, so a JavaScript shell
    and a genuinely short page are both just `failed` — indistinguishable
    without this distribution.
    """
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    sizes = [10, 50, 120, 3000, 4000, 5000, 6000, 20000, 30000, 40000]
    with timing.batch():
        for i, size in enumerate(sizes):
            ident = f"W{i}"
            with timing.track(ident, "web"):
                timing.note(chars=size)
            timing.finish(ident, "indexed")

    line = _lines(caplog, "web_chars")[0]
    assert "n=10" in line
    assert "thin(<200)=30%" in line  # three of ten under the floor


def test_batch_ignores_non_web_kinds_in_the_char_distribution(caplog):
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.batch():
        with timing.track("P1", "patent"):
            timing.note(chars=5)
        timing.finish("P1", "indexed")
    assert _lines(caplog, "web_chars") == []


def test_documents_finished_outside_a_batch_still_log(caplog):
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.track("SOLO", "web"):
        with timing.span("fetch"):
            pass
    timing.finish("SOLO", "indexed")
    assert _lines(caplog, "doc SOLO")


def test_empty_batch_logs_nothing(caplog):
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.batch():
        pass
    assert _lines(caplog, "batch") == []


def test_percentiles():
    assert timing._percentile([], 0.5) == 0
    assert timing._percentile([1], 0.9) == 1
    assert timing._percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert timing._percentile([1, 2, 3, 4, 5], 0.9) == 5


def test_batch_summary_failure_does_not_propagate(caplog, monkeypatch):
    """A metrics bug must never fail an ingest."""
    monkeypatch.setattr(timing, "_log_batch", lambda b: 1 / 0)
    with timing.batch():
        with timing.track("A", "web"):
            pass
        timing.finish("A", "indexed")  # must not raise


def test_track_with_empty_identifier_is_a_noop():
    with timing.track("", "web"):
        with timing.span("fetch"):
            pass
    assert timing._records == {}


def test_http_errors_are_not_counted_as_thin_content(caplog):
    """A 403 is not a JavaScript shell, and no renderer fixes it.

    Collapsing the two would inflate the "thin" rate and argue for a
    dynamic-rendering dependency that cannot help.
    """
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.batch():
        for i in range(3):  # refused by the server
            with timing.track(f"E{i}", "web"):
                timing.note(http=403, chars=0)
            timing.finish(f"E{i}", "failed")
        for i in range(2):  # served, but nearly empty
            with timing.track(f"T{i}", "web"):
                timing.note(http=200, chars=40)
            timing.finish(f"T{i}", "failed")

    line = _lines(caplog, "web_chars")[0]
    assert "n=2" in line, line               # only the served pages
    assert "thin(<200)=100%" in line, line   # both served pages were thin
    assert "http_errors=403:3" in line, line


def test_mineru_pages_are_reported_on_the_document_line(caplog):
    """The parser tier is "hybrid" whether or not MinerU ran, so the escalation
    count is the only per-document signal that the quota bought anything."""
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.track("DOC1", "literature"):
        with timing.span("parse"):
            pass
        timing.note(parser="hybrid", mineru_pages=3)
    timing.finish("DOC1", "indexed")

    line = _lines(caplog, "doc DOC1")[0]
    assert "(hybrid+mineru:3)" in line, line


def test_parser_tier_stays_plain_when_mineru_did_not_run(caplog):
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")
    with timing.track("DOC2", "literature"):
        with timing.span("parse"):
            pass
        timing.note(parser="hybrid")
    timing.finish("DOC2", "indexed")

    line = _lines(caplog, "doc DOC2")[0]
    assert "(hybrid)" in line and "mineru" not in line, line
