"""``datetime.utcnow()`` stays out of the code base (round-3 P3-16).

It is deprecated from Python 3.12 — a DeprecationWarning on every call — and returns a naive
value that reads like local time. The audit counted 87 uses next to 41 timezone-aware ones; the
modules since moved to ``datetime.now(timezone.utc)`` (naive where the column is naive), and the
last runtime straggler now uses :func:`app.clock.utcnow`. This guard keeps it that way: the
deprecated spelling — a call, ``default=datetime.utcnow``, or the equally deprecated
``utcfromtimestamp`` — fails the build in application code, scripts and tests.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from app.clock import utcnow

BACKEND = Path(__file__).resolve().parents[1]
DEPRECATED = {"utcnow", "utcfromtimestamp"}


def _is_datetime_class(node: ast.AST) -> bool:
    """``datetime`` or ``datetime.datetime`` (the module-qualified spelling)."""
    if isinstance(node, ast.Name):
        return node.id == "datetime"
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "datetime"
        and isinstance(node.value, ast.Name)
        and node.value.id == "datetime"
    )


def _offences(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr in DEPRECATED and _is_datetime_class(node.value)
    ]


def test_no_module_uses_the_deprecated_spelling():
    offenders = []
    for folder in ("app", "scripts", "tests"):
        for path in sorted((BACKEND / folder).rglob("*.py")):
            if path == Path(__file__):
                continue
            for line in _offences(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(BACKEND)}:{line}")
    assert not offenders, (
        "datetime.utcnow()/utcfromtimestamp() are deprecated in Python 3.12 — use app.clock.utcnow() "
        "(naive UTC) or datetime.now(timezone.utc) (aware): " + ", ".join(offenders)
    )


def test_the_scan_sees_every_spelling():
    bad = """
import datetime
from datetime import datetime as dt
from sqlalchemy import Column, DateTime
a = datetime.utcnow()
b = datetime.datetime.utcnow()
c = Column(DateTime, default=datetime.utcnow)
d = datetime.utcfromtimestamp(0)
"""
    assert sorted(_offences(bad)) == [5, 6, 7, 8]  # call, module-qualified call, default=, utcfromtimestamp
    ok = "x = datetime.now(timezone.utc)\ny = utcnow()\nz = obj.utcnow()\n"
    assert _offences(ok) == []  # the helper and unrelated objects are fine


def test_the_helper_is_naive_utc_and_current():
    now = utcnow()
    assert now.tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds()) < 5
