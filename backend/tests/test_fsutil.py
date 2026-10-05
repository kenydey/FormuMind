"""``replace_with_retry``: an atomic replace that survives a reader on Windows (round-4).

``test_b3_concurrent_read_write_never_sees_torn_json`` failed on a real Windows runner with
``PermissionError(13, 'Access is denied')``: POSIX replaces a file while it is open elsewhere, Windows does not.
"""
from __future__ import annotations

import os
import sys

import pytest

from app.services import _fsutil


def test_it_is_os_replace_when_nothing_is_wrong(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    _fsutil.replace_with_retry(src, dst)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()


def test_on_windows_a_busy_target_is_retried(tmp_path, monkeypatch):
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 4:
            raise PermissionError(13, "Access is denied")
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(_fsutil.time, "sleep", lambda s: None)
    _fsutil.replace_with_retry("a", "b")
    assert len(calls) == 4


def test_on_windows_it_gives_up_eventually(monkeypatch):
    def always(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "replace", always)
    monkeypatch.setattr(_fsutil.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        _fsutil.replace_with_retry("a", "b")


def test_elsewhere_a_permission_error_is_not_retried(monkeypatch):
    calls = []

    def deny(src, dst):
        calls.append(1)
        raise PermissionError(13, "read-only directory")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(PermissionError):
        _fsutil.replace_with_retry("a", "b")
    assert calls == [1], "a real permission problem on POSIX must surface at once"


def test_every_atomic_write_in_the_app_goes_through_the_helper():
    """A bare os.replace is the Windows bug; only the corrupt-file quarantine rename may stay."""
    import ast
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in sorted(app.rglob("*.py")):
        if path.name == "_fsutil.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "replace"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            ):
                offenders.append(path.relative_to(app.parent).as_posix())
    # The one bare call left is the corrupt-file quarantine rename in literature_manifest (a move, not an atomic write).
    assert offenders == ["app/services/literature_manifest.py"], offenders


def test_unwritable_path_is_unwritable_everywhere(tmp_path):
    target = _fsutil.unwritable_path(tmp_path)
    with pytest.raises(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
