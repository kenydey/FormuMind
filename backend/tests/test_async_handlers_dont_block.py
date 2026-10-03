"""No ``async def`` API handler may run blocking service / database work on the event loop (round-3 P2-9).

An ``async def`` handler that calls a plain function runs it *on the event loop*: for its whole
duration the worker answers nobody else. The round-3 audit found this with a one-off scan —
the chat stream finishing its evidence checks inline (seconds of Crossref + LLM review), structure
recognition waiting up to 180 s, and four upload handlers unzipping / writing / parsing inline.
This test is that scan, kept: a new un-offloaded call fails here instead of surfacing as a stalled
server in production.

A call is fine when it is awaited, runs inside ``run_in_threadpool`` / ``asyncio.to_thread`` /
``run_in_executor``, or belongs to the short allow-list below (each entry says why it cannot block).
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import sys
import warnings
from pathlib import Path
from types import ModuleType

import pytest

API_DIR = Path(__file__).resolve().parents[1] / "app" / "api"
OFFLOAD = {"to_thread", "run_in_threadpool", "run_in_executor"}
SERVICE_PACKAGES = ("app.services", "app.db", "app.pipeline", "app.agents")

# "package.module.function" (as ``services.chat_clarify.apply_assumption_to_structured``) → why it is safe
ALLOWED: dict[str, str] = {
    "services.chat_clarify.apply_assumption_to_structured": "pure model copy, no I/O",
    "services.rag.active_rag_backend": "settings lookup and availability flags, no I/O",
    "db.campaign_store.get_campaign_store": "cached singleton; only the first call per process can probe DataLab (≤ 2 s)",
    "services.http_safe.make_async_client": "constructs a client (shared TLS context), sends nothing",
    "services.errors.log_handled_exception": "writes one log line",
}


def _resolve(module: ModuleType, node: ast.AST, local_imports: dict[str, object]):
    """The object a call target names (module globals + imports local to the handler), or None."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    parts.reverse()
    obj = local_imports.get(parts[0]) or getattr(module, parts[0], None)
    for part in parts[1:]:
        try:
            obj = getattr(obj, part)
        except Exception:  # noqa: BLE001
            return None
    return obj


def _local_imports(fn: ast.AST, package: str) -> dict[str, object]:
    found: dict[str, object] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.ImportFrom) or not (node.module or node.level):
            continue
        try:
            base = importlib.import_module("." * node.level + (node.module or ""), package=package)
        except Exception:  # noqa: BLE001
            continue
        for alias in node.names:
            try:
                found[alias.asname or alias.name] = getattr(base, alias.name)
            except AttributeError:
                try:
                    found[alias.asname or alias.name] = importlib.import_module(f"{base.__name__}.{alias.name}")
                except Exception:  # noqa: BLE001
                    pass
    return found


def blocking_calls(module: ModuleType, source: str, package: str) -> list[tuple[str, int, str]]:
    """``(handler, line, "package.module.function")`` for each un-offloaded, un-awaited service call."""
    hits: list[tuple[str, int, str]] = []
    tree = ast.parse(source)
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)):
        local = _local_imports(fn, package)
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

        exempt: set[int] = set()  # everything inside the arguments of an offloading call
        for call in calls:
            target = call.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name in OFFLOAD:
                exempt.update(id(sub) for sub in ast.walk(call) if sub is not call)
        awaited = {id(n.value) for n in ast.walk(fn) if isinstance(n, ast.Await)}

        for call in calls:
            if id(call) in exempt or id(call) in awaited:
                continue
            obj = _resolve(module, call.func, local)
            if obj is None or not callable(obj) or inspect.iscoroutinefunction(obj):
                continue
            origin = getattr(obj, "__module__", "") or ""
            if origin.startswith(SERVICE_PACKAGES) and inspect.isfunction(obj):
                hits.append((fn.name, call.lineno, f"{origin.split('app.', 1)[1]}.{obj.__name__}"))
    return hits


def test_no_async_handler_runs_blocking_work_on_the_event_loop():
    warnings.filterwarnings("ignore")
    offenders: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        name = f"app.api.{path.stem}"
        module = importlib.import_module(name)
        for handler, line, target in blocking_calls(module, path.read_text(encoding="utf-8"), name):
            if target not in ALLOWED:
                offenders.append(f"{path.name}:{line} async {handler}() calls {target}")
    assert not offenders, (
        "blocking calls on the event loop — wrap them in run_in_threadpool (or move them into a sync "
        "helper that is), or add an ALLOWED entry that says why the call cannot block:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


def test_the_allow_list_does_not_rot():
    """An entry nobody trips any more should go, so the list stays a list of real exceptions."""
    seen: set[str] = set()
    for path in sorted(API_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        name = f"app.api.{path.stem}"
        seen.update(t for _h, _l, t in blocking_calls(importlib.import_module(name), path.read_text(encoding="utf-8"), name))
    stale = sorted(set(ALLOWED) - seen)
    assert not stale, f"ALLOWED entries that no handler needs any more: {stale}"


# ── the scan itself must actually see what it claims to see ─────────────────────


@pytest.fixture
def synthetic(tmp_path):
    source = '''
from fastapi.concurrency import run_in_threadpool
from app.services.skill_install import result_to_dict


async def inline(x):
    return result_to_dict(x)  # blocking call on the loop


async def offloaded(x):
    return await run_in_threadpool(result_to_dict, x)


async def offloaded_in_lambda(x):
    return await run_in_threadpool(lambda: result_to_dict(x))


async def local_import(x):
    from app.services.mcp_import import result_to_dict as other
    return other(x)  # same, through an import local to the handler


def sync_handler(x):
    return result_to_dict(x)  # a plain def runs in the thread pool already
'''
    path = tmp_path / "synthetic_handlers.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("synthetic_handlers", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["synthetic_handlers"] = module
    spec.loader.exec_module(module)
    yield module, source
    sys.modules.pop("synthetic_handlers", None)


def test_the_scan_flags_inline_calls_and_accepts_offloaded_ones(synthetic):
    module, source = synthetic
    hits = blocking_calls(module, source, "synthetic_handlers")
    assert {(handler, target) for handler, _line, target in hits} == {
        ("inline", "services.skill_install.result_to_dict"),
        ("local_import", "services.mcp_import.result_to_dict"),
    }
