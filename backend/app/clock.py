"""The one place that says what "now" means for the naive datetimes stored in the database.

``datetime.utcnow()`` is deprecated from Python 3.12 (it returns a *naive* value that looks
local-time-ish and warns on every call). The database columns here are naive UTC, and comparing a
naive with a timezone-aware datetime raises ``TypeError``, so the replacement keeps the value
naive: aware UTC "now", then drop the tzinfo. ``tests/test_no_deprecated_utcnow.py`` keeps the
deprecated spelling out of the code base.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC "now" — exactly what ``datetime.utcnow()`` returned, without the deprecation."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
