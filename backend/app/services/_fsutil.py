"""File-system helpers whose behaviour differs between POSIX and Windows."""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from pathlib import Path

_RETRIES = 30
_DELAY_S = 0.01


def replace_with_retry(src: str | os.PathLike, dst: str | os.PathLike) -> None:
    """``os.replace`` that survives a reader on Windows.

    POSIX replaces a file atomically even while another process or thread has it open. Windows refuses
    (``PermissionError: [WinError 5] Access is denied``) for as long as any handle without share-delete is open on
    the target — and a status poll reading a JSON file while a worker rewrites it is the normal case in this
    application. The write is retried briefly; elsewhere it is exactly ``os.replace``.
    """
    for attempt in range(_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == _RETRIES - 1:
                raise
            time.sleep(_DELAY_S * min(attempt + 1, 10))


def read_text_with_retry(path: str | os.PathLike, encoding: str = "utf-8") -> str:
    """``Path.read_text`` that survives the instant a writer's ``os.replace`` has the file on Windows.

    There, opening a file that is being replaced can fail with ``PermissionError`` (no share-delete on the open
    handle, a rename in flight) although the file is perfectly healthy. Callers that treat *any* read failure as
    "the file is corrupt" would then move a good file aside. The read is retried briefly; elsewhere it is exactly
    ``read_text``.
    """
    target = Path(path)
    for attempt in range(_RETRIES):
        try:
            return target.read_text(encoding=encoding)
        except PermissionError:
            if sys.platform != "win32" or attempt == _RETRIES - 1:
                raise
            time.sleep(_DELAY_S * min(attempt + 1, 10))
    raise AssertionError("unreachable")  # pragma: no cover


def read_bytes_with_retry(path: str | os.PathLike) -> bytes:
    """:func:`read_text_with_retry` for binary content."""
    target = Path(path)
    for attempt in range(_RETRIES):
        try:
            return target.read_bytes()
        except PermissionError:
            if sys.platform != "win32" or attempt == _RETRIES - 1:
                raise
            time.sleep(_DELAY_S * min(attempt + 1, 10))
    raise AssertionError("unreachable")  # pragma: no cover


def atomic_write_text(path: str | os.PathLike, text: str, encoding: str = "utf-8") -> None:
    """Write *text* so that a reader sees the old file or the new one — never a half-written one.

    ``Path.write_text`` truncates and then writes: a concurrent reader can catch the file empty or cut short (and
    treats it as missing or corrupt). The text goes to a sibling temp file first — named per process and thread, so two
    writers of the same target cannot share one — and is moved into place with :func:`replace_with_retry`.
    """
    target = Path(path)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        replace_with_retry(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def unwritable_path(base: Path) -> Path:
    """A path nothing can be written to on any platform: a file where a directory would have to be."""
    blocker = base / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    return blocker / "child"
