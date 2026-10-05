"""The cross-process file lock, and "nothing under app/ may need a POSIX-only module to import" (round-4).

``artifact_versions`` and ``smart_collections`` did ``import fcntl`` at module level. ``fcntl`` does not exist on
Windows, so ``app.main`` — which imports ``artifact_versions`` through its router list — raised
``ModuleNotFoundError`` on start. The Windows installer finished "successfully" in front of an application that
could not run; nothing in this repository's CI could notice (every job is Linux). It was found by the first run of
the ``installer-windows`` job.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
FILELOCK = APP / "services" / "_filelock.py"

# Modules that exist on one family of platforms only. Importing one at module level makes the whole importing
# module — and everything that imports *it* — unusable on the other.
POSIX_ONLY = {"fcntl", "pwd", "grp", "termios", "resource", "pty", "tty", "syslog", "crypt", "posix", "readline"}
WINDOWS_ONLY = {"msvcrt", "winreg", "_winapi", "winsound"}


def _module_level_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:  # direct children only: an import under `if sys.platform` / `try` is a guard
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name.split(".")[0]) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module.split(".")[0]))
    return found


def test_no_module_imports_a_platform_only_module_unguarded():
    offenders = [
        f"{path.relative_to(APP.parent)}:{line}: import {name}"
        for path in sorted(APP.rglob("*.py"))
        for line, name in _module_level_imports(path)
        if name in POSIX_ONLY | WINDOWS_ONLY
    ]
    assert not offenders, "\n".join(
        ["these imports break the application on one platform (guard them with `if sys.platform` / try-except):"]
        + offenders
    )


def test_the_services_that_lock_files_use_the_shared_helper():
    for name in ("artifact_versions", "smart_collections"):
        source = (APP / "services" / f"{name}.py").read_text(encoding="utf-8")
        assert "from ._filelock import" in source, name
        assert "fcntl." not in source.replace("``fcntl", ""), f"{name} still calls fcntl directly"


# ── POSIX: the real lock ────────────────────────────────────────────────────

def test_a_second_holder_waits_for_the_first(tmp_path):
    from app.services._filelock import lock_exclusive, unlock

    path = tmp_path / "x.lock"
    acquired_at: list[float] = []

    def contender():
        with open(path, "w", encoding="utf-8") as second:
            lock_exclusive(second)
            acquired_at.append(time.monotonic())
            unlock(second)

    with open(path, "w", encoding="utf-8") as first:
        lock_exclusive(first)
        thread = threading.Thread(target=contender)
        thread.start()
        time.sleep(0.3)
        assert not acquired_at, "the second holder got in while the first still held the lock"
        released_at = time.monotonic()
        unlock(first)
    thread.join(10)
    assert acquired_at and acquired_at[0] >= released_at


# ── Windows: the msvcrt branch, run here against a fake ─────────────────────

class _Handle:
    def __init__(self):
        self.seeks: list[int] = []

    def seek(self, pos: int) -> None:
        self.seeks.append(pos)

    def fileno(self) -> int:
        return 7


def _windows_filelock(monkeypatch, locking):
    fake_msvcrt = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
    spec = importlib.util.spec_from_file_location("app.services._filelock_as_windows", FILELOCK)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        patch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        spec.loader.exec_module(module)
    return module


def test_windows_retries_the_non_blocking_lock_until_it_gets_it(monkeypatch):
    calls: list[tuple] = []

    def locking(fd, mode, nbytes):
        calls.append((fd, mode, nbytes))
        if len(calls) < 3:
            raise OSError("locked by another process")

    module = _windows_filelock(monkeypatch, locking)
    handle = _Handle()
    module.lock_exclusive(handle, timeout=5)
    assert calls == [(7, 2, 1)] * 3
    assert handle.seeks == [0, 0, 0], "msvcrt locks from the current position: always the first byte"


def test_windows_gives_up_after_the_timeout(monkeypatch):
    def locking(fd, mode, nbytes):
        raise OSError("locked by another process")

    module = _windows_filelock(monkeypatch, locking)
    started = time.monotonic()
    with pytest.raises(OSError):
        module.lock_exclusive(_Handle(), timeout=0.2)
    assert time.monotonic() - started < 2


def test_windows_unlocks_the_same_byte(monkeypatch):
    calls: list[tuple] = []
    module = _windows_filelock(monkeypatch, lambda fd, mode, nbytes: calls.append((fd, mode, nbytes)))
    handle = _Handle()
    module.unlock(handle)
    assert calls == [(7, 0, 1)] and handle.seeks == [0]


def test_the_windows_branch_does_not_import_fcntl(monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", None)  # an import of it would raise
    _windows_filelock(monkeypatch, lambda *a: None)
