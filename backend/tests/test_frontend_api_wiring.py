"""Nothing in the frontend API layer — or the backend route table — may rot unnoticed (round-3 P2-6).

Two ledgers that used to maintain themselves by accident:

* ``frontend/src/api/methods.ts`` accumulated wrappers nobody calls (ten of them, three of which
  fronted routes that also had no other entry point) — every wrapper must have a caller.
* the backend accumulated documented routes the frontend never reaches. Many of them are *meant*
  to be API-only (agent tools, ops diagnostics); those are listed below **with the reason**, so
  "no screen" is a recorded decision instead of an oversight. A route that gains a caller, or
  disappears, must leave the list — otherwise the list itself rots.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO / "frontend" / "src"

pytestmark = pytest.mark.skipif(not FRONTEND_SRC.is_dir(), reason="frontend sources not present")

# Wrappers deliberately kept without a caller: name → reason. Prefer deleting the wrapper.
UNCALLED_WRAPPERS: dict[str, str] = {}

_AGENT_DRIVEN = "agent-driven: the chat / MCP agent calls it, no screen drives it"
_OPS = "operations diagnostic (curl / scripts/ monitoring); deliberately no screen"

# Documented backend routes the frontend does not call, and why that is fine.
API_ONLY_ROUTES: dict[str, str] = {
    "/api/artifacts/versions/{}": "version metadata; screens read versions through the lineage listing and the diff",
    "/api/artifacts/versions/{}/content": f"artifact publication workflow (staging → submit → finalize): {_AGENT_DRIVEN}",
    "/api/artifacts/versions/{}/evidence": f"artifact publication workflow: {_AGENT_DRIVEN}",
    "/api/artifacts/versions/{}/evidence/verify": f"artifact publication workflow: {_AGENT_DRIVEN}",
    "/api/artifacts/versions/{}/submit": f"artifact publication workflow: {_AGENT_DRIVEN}",
    "/api/artifacts/versions/{}/finalize": f"artifact publication workflow: {_AGENT_DRIVEN}",
    "/api/artifacts/versions/{}/verify": f"artifact publication workflow: {_AGENT_DRIVEN}",
    "/api/connectors/builtin/{}/search": f"built-in connector search tool: {_AGENT_DRIVEN}; the UI only toggles connectors",
    "/api/connectors/mcp": "bulk replace of the MCP server list; the UI imports and toggles/removes servers one by one",
    "/api/connectors/mcp/call": f"MCP tool-call proxy: {_AGENT_DRIVEN}",
    "/api/formulation-skills": "read server-side by the recommender; the skills screen uses the merged /api/skills catalog",
    "/api/formulation-skills/{}": "read server-side by the recommender; the skills screen uses the merged /api/skills catalog",
    "/api/kb/golden-questions": "lists the curated golden retrieval set; the quality screen only runs it (/api/kb/golden-eval/run)",
    "/api/kb/relevance-shadow/stats": _OPS,
    "/api/ops/datalab-orphans": _OPS,
    "/api/ops/datalab-orphans/cleanup": _OPS,
    "/api/ops/evidence-stats": _OPS,
    "/api/ops/kb-health": _OPS,
    "/api/ops/recommend-stats": _OPS,
    "/api/reports/capabilities": "which export formats this deployment can produce; the UI offers all four and shows the 503",
    "/api/session-plans": f"plan submission: {_AGENT_DRIVEN}; the UI approves or rejects pending plans",
    "/api/session-plans/{}/advance": f"marking a plan step done: {_AGENT_DRIVEN}; the UI only approves or rejects",
    "/api/skills/installed": "the skills screen reads the merged /api/skills catalog and checks updates per skill",
    "/api/skills/{}": "the skills screen reads the merged /api/skills catalog",
    "/api/sources/{}/detail": "one-call paper detail for agents; screens compose it from the /api/kb/sources/{}/… calls",
    "/api/wiki/dossier/patch": "section-level dossier patch for agents; screens refresh whole dossiers",
    "/api/wiki/dossier/{}/pack": "dossier pack for agents and exports",
    "/api/wiki/pages/{}": "wiki pages are opened by path (/api/wiki/by-path)",
}


# ── reading the frontend ─────────────────────────────────────────────────────

# ``${...}`` with one level of nested template literal / braces: ``${qs ? `?${qs}` : ""}``.
_EXPR = r"\$\{(?:[^{}`]|`[^`]*`|\{[^{}]*\})*\}"
_EXPR_RE = re.compile(_EXPR)
_LITERAL = re.compile(
    r"'(/api/(?:[^'\\\n])*)'"
    r"|\"(/api/(?:[^\"\\\n])*)\""
    r"|`(/api/(?:[^`\\$]|\\.|" + _EXPR + r")*)`"
)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(^|[^:'\"`])//[^\n]*", r"\1", text)


def _frontend_files() -> list[Path]:
    return [
        p
        for p in sorted(FRONTEND_SRC.rglob("*"))
        if p.suffix in (".ts", ".tsx") and ".test." not in p.name
    ]


def _normalize(literal: str) -> str:
    """``/api/a/${id}/b${qs}`` → ``/api/a/{}/b``: params become ``{}``; query / optional suffixes drop."""
    out: list[str] = []
    i = 0
    while i < len(literal):
        m = _EXPR_RE.match(literal, i)
        if m:
            if out and out[-1] != "/":  # glued onto a segment: a query string or optional suffix
                break
            out.append("{}")
            i = m.end()
            continue
        if literal[i] in "?#":
            break
        out.append(literal[i])
        i += 1
    return "".join(out)


def _frontend_api_literals() -> set[str]:
    found: set[str] = set()
    for path in _frontend_files():
        for m in _LITERAL.finditer(_strip_comments(path.read_text(encoding="utf-8"))):
            found.add(_normalize(m.group(1) or m.group(2) or m.group(3)))
    return found


def _is_called(route: str, literals: set[str]) -> bool:
    """Exact match on the normalised path. (A ``{}`` must not stand for a *literal* segment: that
    would make ``/api/connectors/mcp/{}`` "cover" the sibling route ``/api/connectors/mcp/call``.)"""
    return route in literals


def _documented_routes() -> set[str]:
    """Paths in the OpenAPI schema (``app.routes`` is a tree of included routers, not a flat list)."""
    from app.main import app

    return {
        re.sub(r"\{[^}]*\}", "{}", path)
        for path in app.openapi()["paths"]
        if path.startswith("/api")
    }


def _api_method_names() -> list[str]:
    text = (FRONTEND_SRC / "api" / "methods.ts").read_text(encoding="utf-8")
    start = text.index("export const apiMethods = {")
    member = re.compile(r"^  (?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:(<]")
    names: list[str] = []
    for line in text[start:].splitlines()[1:]:
        if line.startswith("};"):
            break
        m = member.match(line)
        if m:
            names.append(m.group(1))
    return names


# ── guards ───────────────────────────────────────────────────────────────────


def test_the_reader_finds_what_it_is_supposed_to_find():
    """Sanity of the scanners themselves — an empty result would make both guards vacuous."""
    assert len(_api_method_names()) > 200
    literals = _frontend_api_literals()
    assert "/api/formulations/recommend" in literals
    assert "/api/wiki/literature/{}/export.bib" in literals  # `${qs}` glued on: dropped
    assert "/api/kg/graph" in literals  # `${qs ? `?${qs}` : ""}` with a nested template literal
    assert len(_documented_routes()) > 200


def test_every_api_wrapper_has_a_caller():
    sources = {
        p: _strip_comments(p.read_text(encoding="utf-8"))
        for p in _frontend_files()
        if p != FRONTEND_SRC / "api" / "methods.ts"
    }
    uncalled = [
        name
        for name in _api_method_names()
        if name not in UNCALLED_WRAPPERS
        and not any(re.search(rf"\b{name}\b", text) for text in sources.values())
    ]
    assert not uncalled, (
        "wrappers in api/methods.ts that nothing calls — wire them into a screen or delete them "
        f"(a wrapper nobody calls is an untested contract): {uncalled}"
    )
    stale = [n for n in UNCALLED_WRAPPERS if n not in _api_method_names()]
    assert not stale, f"UNCALLED_WRAPPERS lists wrappers that no longer exist: {stale}"


def test_every_documented_route_is_called_by_the_frontend_or_recorded_as_api_only():
    literals = _frontend_api_literals()
    routes = _documented_routes()
    uncalled = sorted(r for r in routes if r not in API_ONLY_ROUTES and not _is_called(r, literals))
    assert not uncalled, (
        "documented backend routes with no frontend caller — add the screen, or record why the "
        f"route is API-only in API_ONLY_ROUTES: {uncalled}"
    )


def test_the_api_only_list_does_not_rot():
    literals = _frontend_api_literals()
    routes = _documented_routes()
    gone = sorted(r for r in API_ONLY_ROUTES if r not in routes)
    assert not gone, f"API_ONLY_ROUTES lists routes that no longer exist: {gone}"
    now_called = sorted(r for r in API_ONLY_ROUTES if _is_called(r, literals))
    assert not now_called, f"the frontend now calls these — drop them from API_ONLY_ROUTES: {now_called}"
