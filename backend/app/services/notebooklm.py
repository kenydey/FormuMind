"""Google NotebookLM as a retrieval source.

Wraps the unofficial ``notebooklm-py`` SDK (browser-session auth) so a notebook id (typically per research project) can be queried like any
other evidence source. The SDK is async
and optional: when the library is missing, the feature is disabled, or the
stored login session is absent, every call degrades silently to ``[]`` — exactly
like the other ``search_*`` adapters in ``literature.py``.

Auth is a one-time operational step (``notebooklm login``) that writes a browser
session file; this module never handles Google credentials directly.

NOTE: NotebookLM has no official public API. ``notebooklm-py`` uses undocumented
Google endpoints that may change without notice; gate production use behind
``FORMUMIND_NOTEBOOKLM_ENABLED``.
"""
from __future__ import annotations

import logging
from .errors import log_handled_exception
import asyncio
import concurrent.futures
import os
import subprocess
import sys

from ..config import get_settings
from ..domain.schemas import Evidence

logger = logging.getLogger(__name__)

NOTEBOOKLM_URL = "https://notebooklm.google.com"


def _resolve_notebook_id(notebook_id: str | None = None) -> str | None:
    """Prefer an explicit (per-project) id; fall back to legacy global settings."""
    explicit = (notebook_id or "").strip()
    if explicit:
        return explicit
    s = get_settings()
    legacy = (s.notebooklm_notebook_id or "").strip()
    return legacy or None


def _auth_ready() -> bool:
    """Global auth prerequisites: feature enabled + session file + library."""
    s = get_settings()
    if not s.notebooklm_enabled:
        return False
    if not os.path.exists(s.notebooklm_storage_path):
        return False
    try:
        import notebooklm  # type: ignore  # noqa: F401
        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def _notebooklm_available(notebook_id: str | None = None) -> bool:
    """Auth ready + a notebook id (per-project override or legacy global)."""
    if not _auth_ready():
        return False
    return _resolve_notebook_id(notebook_id) is not None


def _run_async(coro):
    """Run an async coroutine from a sync context.

    The ``/api/search`` route is a sync ``def`` (FastAPI runs it in a worker
    thread with no running loop), so ``asyncio.run`` works directly. If a loop
    is already running, fall back to a dedicated thread to avoid ``RuntimeError``.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _to_evidence(
    result, query: str, limit: int, *, notebook_id: str | None = None
) -> list[Evidence]:
    """Map a notebooklm-py chat result into Evidence objects.

    ``chat.ask`` returns a synthesised answer (``result.answer``) optionally with
    citations. When citations are exposed we emit one Evidence per citation;
    otherwise the answer itself becomes a single Evidence.
    """
    notebook_id = _resolve_notebook_id(notebook_id) or "notebook"
    answer = (getattr(result, "answer", None) or str(result or "")).strip()

    citations = getattr(result, "citations", None) or getattr(result, "sources", None) or []
    out: list[Evidence] = []
    for i, c in enumerate(citations[:limit]):
        text = str(getattr(c, "text", None) or getattr(c, "snippet", None) or c).strip()
        if not text:
            continue
        title = str(getattr(c, "title", None) or f"NotebookLM citation {i + 1}")
        ident = str(getattr(c, "id", None) or f"{notebook_id}#cite{i + 1}")
        out.append(Evidence(
            source="NotebookLM",
            identifier=ident,
            title=title[:200],
            snippet=text[:500],
            relevance=round(max(0.4, 0.9 - i * 0.1), 2),
        ))

    if not out and answer:
        out.append(Evidence(
            source="NotebookLM",
            identifier=f"{notebook_id}#answer",
            title=(query or "NotebookLM answer")[:200],
            snippet=answer[:500],
            relevance=0.85,
        ))
    return out[:limit]


async def _aquery(
    query: str, limit: int, *, notebook_id: str | None = None
) -> list[Evidence]:
    from notebooklm import NotebookLMClient  # type: ignore

    s = get_settings()
    nid = _resolve_notebook_id(notebook_id)
    if not nid:
        return []
    async with NotebookLMClient.from_storage(s.notebooklm_storage_path) as client:  # pragma: no cover - network
        result = await client.chat.ask(nid, query)
    return _to_evidence(result, query, limit, notebook_id=nid)


def search_notebooklm(
    query: str, limit: int = 5, *, notebook_id: str | None = None
) -> list[Evidence]:
    """Query a NotebookLM notebook; any failure → [] (silent fallback).

    ``notebook_id`` is the per-project notebook. When omitted, the legacy global
    ``FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID`` is used as a migration fallback.
    """
    if not _notebooklm_available(notebook_id):
        return []
    try:
        return _run_async(_aquery(query, limit, notebook_id=notebook_id))
    except Exception:
        return []


def _lib_installed() -> bool:
    try:
        import notebooklm  # type: ignore  # noqa: F401
        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def can_launch_browser() -> bool:
    """Heuristic: can this machine pop a real browser window for `notebooklm login`?

    True on desktop macOS/Windows, or on Linux with an X11/Wayland display. False
    on headless/remote servers — the UI then shows the manual fallback instead.
    """
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def set_runtime_config(
    enabled: bool | None = None, notebook_id: str | None = None
) -> dict:
    """Mutate NotebookLM settings in-memory at runtime (mirrors api/settings.py),
    so users configure it from the UI without editing environment variables."""
    s = get_settings()
    if enabled is not None:
        object.__setattr__(s, "notebooklm_enabled", bool(enabled))
    if notebook_id is not None:
        object.__setattr__(s, "notebooklm_notebook_id", notebook_id or None)
    return get_setup_status()


def start_login() -> dict:
    """Trigger the one-time Google authorization for NotebookLM.

    When a browser can be launched on the backend machine, spawn the allowlisted
    ``notebooklm login`` CLI (detached, non-blocking) which opens a Google login
    window; the SDK saves the browser session itself. On headless/remote backends
    (or when the lib is missing) return a ``manual`` payload so the UI can guide
    the user instead. The command is a fixed literal — no user input is ever
    interpolated into the subprocess.
    """
    if not _lib_installed():
        return {
            "started": False,
            "mode": "manual",
            "reason": "library_missing",
            "hint": "先在「依赖管理」安装 notebooklm-py，再完成 Google 授权",
            "command": "pip install -e '.[notebooklm]'",
            "manual_url": NOTEBOOKLM_URL,
        }
    if not can_launch_browser():
        return {
            "started": False,
            "mode": "manual",
            "reason": "no_display",
            "hint": "后端无图形界面，请在运行后端的机器上执行 `notebooklm login` 完成 Google 授权（一次性）",
            "command": "notebooklm login",
            "manual_url": NOTEBOOKLM_URL,
        }
    try:
        # Fixed, allowlisted command — never built from request data.
        subprocess.Popen(
            ["notebooklm", "login"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "started": True,
            "mode": "browser",
            "reason": None,
            "hint": "已在本机打开 Google 登录窗口，完成授权后稍候，状态将自动变为已就绪。",
            "command": "notebooklm login",
            "manual_url": NOTEBOOKLM_URL,
        }
    except Exception as exc:  # pragma: no cover - environment-dependent
        return {
            "started": False,
            "mode": "manual",
            "reason": "launch_failed",
            "hint": f"自动启动失败（{exc}）。请手动执行 `notebooklm login` 完成 Google 授权。",
            "command": "notebooklm login",
            "manual_url": NOTEBOOKLM_URL,
        }


def get_setup_status() -> dict:
    """Return detailed NotebookLM configuration status with user-actionable hints.

    Carries the original ``available/offline_fallback/reason/hint`` contract plus
    granular booleans (``lib_installed``/``enabled``/``notebook_id_set``/
    ``session_present``/``can_launch_browser``) the auth UI uses to decide which
    control to show. Extra keys are ignored by the ``SourceStatus`` model used on
    ``/api/search/status``.
    """
    s = get_settings()
    lib_ok = _lib_installed()
    session_present = bool(
        s.notebooklm_storage_path and os.path.exists(s.notebooklm_storage_path)
    )
    auth_ready = bool(lib_ok and s.notebooklm_enabled and session_present)
    base = {
        "lib_installed": lib_ok,
        "enabled": bool(s.notebooklm_enabled),
        # Legacy global notebook id (migration only). Prefer per-project ids.
        "notebook_id_set": bool(s.notebooklm_notebook_id),
        "notebook_id": s.notebooklm_notebook_id,
        "session_present": session_present,
        "can_launch_browser": can_launch_browser(),
        "auth_ready": auth_ready,
        "offline_fallback": False,
    }

    if not lib_ok:
        base.update(
            available=False,
            reason="library_missing",
            hint="在「依赖管理」安装 notebooklm-py，然后点击「授权登录」完成 Google 授权",
        )
    elif not s.notebooklm_enabled:
        base.update(
            available=False,
            reason="not_enabled",
            hint="在全局设置中启用 NotebookLM，并完成 Google 授权；Notebook ID 请在各项目中配置",
        )
    elif not session_present:
        base.update(
            available=False,
            reason="session_missing",
            hint="点击「授权登录」完成 Google 账号授权（一次性操作）",
        )
    else:
        # Auth is ready. Per-project notebook id is configured in the project UI.
        base.update(
            available=True,
            reason=None,
            hint="全局授权已就绪。请在各研究项目的信息类别中勾选 NotebookLM 并填写该项目的 Notebook ID",
        )
    return base
