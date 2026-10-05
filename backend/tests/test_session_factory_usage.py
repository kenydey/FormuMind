"""``default_session_factory()`` returns a *factory*; two defects in a row treated it as a session / engine (round-4).

* ``with default_session_factory() as session`` — a ``sessionmaker`` is not a context manager, so the
  ``TypeError`` was swallowed by the surrounding ``except Exception`` and the Datalab item-id lookup for
  experiment attachments never worked.
* ``default_session_factory().bind`` — no such attribute (``sessionmaker`` keeps it in ``.kw``), swallowed
  by the fail-open handler, so Text2SQL's structured route never ran.

Both hid behind broad ``except`` blocks and behind tests that injected the engine / factory by hand.
This is the scan, kept: a new one fails here instead of surfacing as a feature that never switches on.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.db.database import default_engine, default_session_factory

APP = Path(__file__).resolve().parents[1] / "app"
FACTORY_CALLS = {"default_session_factory", "make_session_factory"}
SESSION_ONLY_ATTRS = {
    "bind", "execute", "query", "add", "add_all", "commit", "rollback", "flush", "get", "scalar",
    "scalars", "begin", "close", "merge", "delete", "refresh",
}


def _callee(node: ast.Call) -> str:
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


def misuse(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and _callee(ctx) in FACTORY_CALLS:
                    found.append((node.lineno, f"`with {_callee(ctx)}()` — call the factory once more to get a Session"))
        elif isinstance(node, ast.Attribute) and node.attr in SESSION_ONLY_ATTRS:
            if isinstance(node.value, ast.Call) and _callee(node.value) in FACTORY_CALLS:
                found.append((node.lineno, f"`{_callee(node.value)}().{node.attr}` — a session factory has no `{node.attr}`"))
    return found


def test_nothing_in_app_treats_a_session_factory_as_a_session():
    offenders = [
        f"{path.relative_to(APP.parent)}:{line}: {why}"
        for path in sorted(APP.rglob("*.py"))
        for line, why in misuse(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "\n".join(offenders)


def test_the_scan_sees_both_shapes_of_the_mistake():
    bad = (
        "def a():\n"
        "    with default_session_factory() as s:\n"
        "        pass\n"
        "def b():\n"
        "    return default_session_factory().bind\n"
        "def fine():\n"
        "    with default_session_factory()() as s:\n"
        "        return default_session_factory()\n"
    )
    assert [line for line, _ in misuse(bad)] == [2, 5]


def test_the_engine_has_a_public_accessor():
    assert default_session_factory().kw["bind"] is default_engine()


@pytest.mark.parametrize("name", ["default_engine", "default_session_factory"])
def test_both_accessors_follow_a_database_url_change(tmp_path, monkeypatch, name):
    from app.config import get_settings
    from app.db import database

    accessor = getattr(database, name)
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/one.db")
    get_settings.cache_clear()
    first = accessor()
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/two.db")
    get_settings.cache_clear()
    assert accessor() is not first
    get_settings.cache_clear()
