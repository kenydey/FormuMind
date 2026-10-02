"""User-visible parse notices (page-cap truncation, OCR fallbacks, …).

Deep parse code (``hybrid_parse`` / ``pdf_local`` / ``rapidocr_local``) can
only log today — the ingest API response never tells the user that pages
were dropped. This module is a tiny contextvar collector: parse code calls
:func:`note`, the ingest entry point (``ingest_file`` / ``ingest_url``)
drains the buffer into ``IngestOutcome.warnings``.

Never raises and never changes behaviour: :func:`note` is a no-op outside a
:func:`collect` block, so a metrics bug cannot fail an ingest.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

_notices: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "parse_notices", default=None
)


@contextmanager
def collect() -> Iterator[list[str]]:
    """Open a notice buffer for the current context; yields the live list."""
    buf: list[str] = []
    token = _notices.set(buf)
    try:
        yield buf
    finally:
        _notices.reset(token)


def note(message: str) -> None:
    """Append a user-visible notice; no-op when no collector is active."""
    buf = _notices.get()
    if buf is not None and message and message not in buf:
        buf.append(message)
