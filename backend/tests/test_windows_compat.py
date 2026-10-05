"""Things that only break on Windows, found by running the backend there (round-4).

The first run of the ``installer-windows`` CI job reached the application for the first time and found, in order:
``import fcntl`` at module level (the app could not be imported — see ``test_filelock``), then
``os.O_DIRECTORY`` (every write of a smart collection raised ``AttributeError``: Windows has no such flag and the
``except OSError`` beside it does not catch it). These tests pin the class on Linux, where CI is cheap, and the job
checks the rest on a real Windows runner.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# `os` attributes that exist on POSIX only. Touch them through getattr(os, name, None) or not at all.
POSIX_ONLY_OS = {
    "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "fork", "forkpty", "getuid", "geteuid", "getgid",
    "setuid", "setsid", "killpg", "getpgid", "chown", "mkfifo", "nice", "uname",
}


def _py_files():
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_posix_only_os_attributes_are_not_touched_directly():
    offenders = []
    for path in _py_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr in POSIX_ONLY_OS
            ):
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}: os.{node.attr}")
    assert not offenders, "\n".join(
        ["these raise AttributeError on Windows (use getattr(os, name, None) and skip when it is missing):"] + offenders
    )


def test_no_hardcoded_tmp_paths():
    """``/tmp/...`` resolves to ``<current drive>:\\tmp\\...`` on Windows — usually absent, never the temp dir."""
    offenders = []
    for path in _py_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(("/tmp/", "/tmp")):
                if node.value == "/tmp" or node.value.startswith("/tmp/"):
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}: {node.value!r}")
    assert not offenders, "\n".join(["use tempfile.gettempdir():"] + offenders)


def test_the_task_directory_defaults_to_the_platform_temp_dir(monkeypatch):
    import tempfile

    from app.services import tech_report
    from app.worker import task_progress, tasks

    monkeypatch.delenv("FORMUMIND_TASK_DIR", raising=False)
    expected = Path(tempfile.gettempdir()) / "formumind_tasks"
    assert tech_report._task_persist_dir() == expected
    assert task_progress._task_dir() == expected
    assert tasks._task_persist_dir() == expected
    monkeypatch.setenv("FORMUMIND_TASK_DIR", "/somewhere/else")
    assert task_progress._task_dir() == Path("/somewhere/else")


def test_directory_fsync_is_skipped_where_the_flag_does_not_exist(tmp_path, monkeypatch):
    from app.services import smart_collections

    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    smart_collections._fsync_dir(tmp_path)  # must not raise AttributeError


def test_directory_fsync_still_runs_where_it_is_supported(tmp_path, monkeypatch):
    if not hasattr(os, "O_DIRECTORY"):
        pytest.skip("no os.O_DIRECTORY on this platform")
    from app.services import smart_collections

    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real_fsync(fd))
    smart_collections._fsync_dir(tmp_path)
    assert len(synced) == 1


def test_the_cjk_font_search_includes_windows(monkeypatch):
    from app.services.wiki import report_export

    monkeypatch.setenv("WINDIR", r"D:\Win")
    # candidates are computed at import; the Windows ones must at least be present for the default WINDIR
    names = [Path(p).name for p in report_export._CJK_FONT_CANDIDATES]
    assert "msyh.ttc" in names and "simsun.ttc" in names


def test_text_files_are_read_and_written_with_an_explicit_encoding():
    """Without ``encoding=`` Python uses the locale's code page on Windows (cp1252 / cp936): a Chinese document
    came back as mojibake or as ``UnicodeDecodeError: 'charmap' codec can't decode byte`` — which is how four of
    this repository's own tests failed there. Applies to app/ and tests/ alike."""
    offenders = []
    for root in (APP, APP.parent / "tests"):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
                    positional = 1 if func.attr == "read_text" else 2  # read_text(encoding) / write_text(data, encoding)
                    if len(node.args) >= positional or any(kw.arg == "encoding" for kw in node.keywords):
                        continue
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}: .{func.attr}()")
                elif isinstance(func, ast.Name) and func.id == "open":
                    mode = None
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        mode = node.args[1].value
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = kw.value.value
                    if isinstance(mode, str) and "b" in mode:
                        continue
                    if len(node.args) >= 4 or any(kw.arg == "encoding" for kw in node.keywords):
                        continue
                    if path.name == "_filelock.py" or "lock" in ast.unparse(node.args[0]).lower():
                        continue  # lock files are never read: their text mode is irrelevant
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}: open()")
    assert not offenders, "\n".join(["pass encoding='utf-8' (or open in binary mode):"] + offenders)
