"""Google NotebookLM as a retrieval source.

Wraps the unofficial ``notebooklm-py`` SDK (browser-session auth) so a single
fixed notebook can be queried like any other evidence source. The SDK is async
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

import asyncio
import concurrent.futures
import os

from ..config import get_settings
from ..domain.schemas import Evidence


def _notebooklm_available() -> bool:
    """Enabled + notebook id configured + session file present + lib importable."""
    s = get_settings()
    if not s.notebooklm_enabled or not s.notebooklm_notebook_id:
        return False
    if not os.path.exists(s.notebooklm_storage_path):
        return False
    try:
        import notebooklm  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


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


def _to_evidence(result, query: str, limit: int) -> list[Evidence]:
    """Map a notebooklm-py chat result into Evidence objects.

    ``chat.ask`` returns a synthesised answer (``result.answer``) optionally with
    citations. When citations are exposed we emit one Evidence per citation;
    otherwise the answer itself becomes a single Evidence.
    """
    s = get_settings()
    notebook_id = s.notebooklm_notebook_id or "notebook"
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


async def _aquery(query: str, limit: int) -> list[Evidence]:
    from notebooklm import NotebookLMClient  # type: ignore

    s = get_settings()
    async with NotebookLMClient.from_storage(s.notebooklm_storage_path) as client:  # pragma: no cover - network
        result = await client.chat.ask(s.notebooklm_notebook_id, query)
    return _to_evidence(result, query, limit)


def search_notebooklm(query: str, limit: int = 5) -> list[Evidence]:
    """Query the fixed NotebookLM notebook; any failure → [] (silent fallback)."""
    if not _notebooklm_available():
        return []
    try:
        return _run_async(_aquery(query, limit))
    except Exception:
        return []


def get_setup_status() -> dict:
    """Return detailed NotebookLM configuration status with user-actionable hints."""
    try:
        import notebooklm  # type: ignore  # noqa: F401
        lib_ok = True
    except Exception:
        lib_ok = False

    if not lib_ok:
        return {
            "available": False,
            "offline_fallback": False,
            "reason": "library_missing",
            "hint": "pip install -e '.[notebooklm]'，然后运行 notebooklm login 完成 Google 授权",
        }

    s = get_settings()
    if not s.notebooklm_enabled:
        return {
            "available": False,
            "offline_fallback": False,
            "reason": "not_enabled",
            "hint": "设置环境变量 FORMUMIND_NOTEBOOKLM_ENABLED=true",
        }
    if not s.notebooklm_notebook_id:
        return {
            "available": False,
            "offline_fallback": False,
            "reason": "no_notebook_id",
            "hint": "设置 FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID=your-notebook-id",
        }
    if not os.path.exists(s.notebooklm_storage_path):
        return {
            "available": False,
            "offline_fallback": False,
            "reason": "session_missing",
            "hint": "运行 notebooklm login 完成 Google 账号授权（一次性操作）",
        }
    return {"available": True, "offline_fallback": False, "reason": None, "hint": None}
