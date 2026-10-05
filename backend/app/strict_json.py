"""Request bodies are JSON as RFC 8259 defines it: ``NaN`` and ``Infinity`` are Python's extension, not JSON.

``json.loads`` accepts the literals ``NaN`` / ``Infinity`` / ``-Infinity`` by default and pydantic ``float`` fields accept
the values, so any numeric field of any endpoint could be handed a non-finite number — which nothing downstream expects.
A fuzz of every documented operation found 500s from ``objectives[].weight = Infinity`` on four endpoints (DOE, recommend,
loop), ``days = NaN`` on the retention purge, ``step_idx = Infinity`` on session plans; and a non-finite value that
*is* stored makes every later response fail to serialise (the response encoder refuses NaN), so one bad request can
leave a record unreadable. Browsers never send them (``JSON.stringify(NaN)`` is ``null``), so rejecting them costs no
legitimate client anything.

FastAPI reads bodies through ``Request.json()``; this replaces it with a strict variant, process-wide, so no router
has to opt in. A rejected body answers 422 like any other malformed JSON.
"""
from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request

_INSTALLED = False


def _reject_constant(name: str) -> Any:
    raise json.JSONDecodeError(f"{name} is not a valid JSON number", name, 0)


async def _strict_json(self: Request) -> Any:
    if not hasattr(self, "_json"):
        body = await self.body()
        self._json = json.loads(body, parse_constant=_reject_constant)
    return self._json


def install() -> None:
    """Idempotently make ``Request.json()`` reject NaN / Infinity."""
    global _INSTALLED
    if not _INSTALLED:
        Request.json = _strict_json  # type: ignore[method-assign]
        _INSTALLED = True
