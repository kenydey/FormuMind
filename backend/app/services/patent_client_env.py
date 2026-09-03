"""Scoped os.environ for legacy patent_client SDK calls (not runtime state storage)."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def epo_ops_env(
    consumer_key: str | None,
    consumer_secret: str | None,
) -> Iterator[None]:
    """Set EPO OPS env vars only for the duration of a patent_client call."""
    updates = {
        "EPO_OPS_CONSUMER_KEY": consumer_key,
        "EPO_OPS_CONSUMER_SECRET": consumer_secret,
    }
    previous: dict[str, str | None] = {}
    try:
        for key, value in updates.items():
            if not value:
                continue
            previous[key] = os.environ.get(key)
            os.environ[key] = value
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
