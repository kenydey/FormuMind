"""State that is written and never read is a switch wired to nothing (round-5).

``DeepResearchEngine.__init__`` copied three credentials out of Settings into private attributes - ``_epo_consumer_key``,
``_epo_consumer_secret``, ``_uspto_api_key`` - and ``_openalex_mailto`` beside them, and nothing ever read any of them.
That was enough to satisfy ``test_settings_wiring_guard`` (the *field* is "read" by the assignment), so the Settings page
kept offering a "USPTO Open Data" key that no code used. ``SessionMemoryService._initialized`` was the same shape. This
scan keeps the pattern out: a private attribute assigned on ``self`` has to be read somewhere in its module (as
``self._x``, as ``obj._x``, or by name through ``getattr`` / ``hasattr`` / ``setattr``).
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def dead_private_attributes(source: str) -> list[tuple[str, int]]:
    """``(name, line)`` of private ``self._x`` attributes the module assigns and never reads."""
    tree = ast.parse(source)
    stored: dict[str, int] = {}
    read: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.ctx, ast.Store):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                    and node.attr.startswith("_")
                    and not node.attr.startswith("__")
                ):
                    stored.setdefault(node.attr, node.lineno)
            else:
                read.add(node.attr)  # self._x or obj._x - whoever holds the object
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"getattr", "hasattr", "setattr", "delattr"}:
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                read.add(node.args[1].value)
    return sorted(((name, line) for name, line in stored.items() if name not in read), key=lambda item: item[1])


def test_no_module_stores_private_state_it_never_reads():
    offenders = [
        f"{path.relative_to(APP.parent)}:{line}: self.{name} is assigned and never read"
        for path in sorted(APP.rglob("*.py"))
        for name, line in dead_private_attributes(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "\n".join(offenders)


def test_the_scan_sees_the_pattern_it_exists_for():
    source = (
        "class Engine:\n"
        "    def __init__(self, settings):\n"
        "        self._settings = settings\n"
        "        self._uspto_api_key = settings.uspto_api_key\n"
        "    def run(self):\n"
        "        return self._settings\n"
    )
    assert dead_private_attributes(source) == [("_uspto_api_key", 4)]


def test_the_scan_accepts_every_way_of_reading():
    source = (
        "class A:\n"
        "    def __init__(self):\n"
        "        self._a = 1\n"
        "        self._b = 2\n"
        "        self._c = 3\n"
        "        self._d = 4\n"
        "    def a(self):\n"
        "        return self._a\n"
        "    def b(self, other):\n"
        "        return other._b\n"
        "    def c(self):\n"
        "        return getattr(self, '_c')\n"
        "    def d(self):\n"
        "        return hasattr(self, '_d')\n"
        "class Dunder:\n"
        "    def __init__(self):\n"
        "        self.__private = 1\n"
        "        self.public = 2\n"
    )
    assert dead_private_attributes(source) == []
