"""The one place that says what "now" means for the naive datetimes stored in the database.

``datetime.utcnow()`` is deprecated from Python 3.12 (it returns a *naive* value that looks
local-time-ish and warns on every call). The database columns here are naive UTC, and comparing a
naive with a timezone-aware datetime raises ``TypeError``, so the replacement keeps the value
naive: aware UTC "now", then drop the tzinfo. ``tests/test_no_deprecated_utcnow.py`` keeps the
deprecated spelling out of the code base.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

_TICK = timedelta(microseconds=1)
# A clock that went backwards by more than this was *set* (NTP step, manual change), not merely coarse: take
# it at its word instead of freezing every timestamp at the old high-water mark until the wall clock catches up.
_MAX_SKEW = timedelta(seconds=1)

_lock = threading.Lock()
_last: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow() -> datetime:
    """Naive UTC "now" — what ``datetime.utcnow()`` returned, without the deprecation — and *strictly
    increasing* within a process.

    Windows' wall clock advances in steps of ~15.6 ms, so two rows created inside one step used to get the
    same ``created_at``, and every ``ORDER BY created_at DESC`` ("newest first") then returned them in an
    arbitrary order: two rule versions saved back to back listed the older one first, and so would the
    "most recently adopted" recommendation. Where the OS clock repeats (or lags) a value, this one adds a
    microsecond instead.
    """
    global _last
    now = _now()
    with _lock:
        if _last is not None and now <= _last and _last - now < _MAX_SKEW:
            now = _last + _TICK
        _last = now
    return now
