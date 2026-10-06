"""``scripts/apply_patches.py`` applies the vendored library patches without ``git`` (round-5).

The script shelled out to ``git apply``. ``python:3.11-slim`` has no git, so the Dockerfile's ``RUN python
scripts/apply_patches.py`` died with ``FileNotFoundError: 'git'`` and the image did not build at all - the first thing
``scripts/docker_smoke.sh`` found, because nothing had ever built it. The patches are now applied in Python, hunk by hunk.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("apply_patches_under_test", BACKEND / "scripts" / "apply_patches.py")
ap = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ap  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(ap)

PATCHES = sorted((BACKEND / "patchspec").rglob("*.patch"))


def _diff(path: str, *hunks: tuple[int, list[str], list[str]]) -> str:
    """A unified diff touching ``path``; each hunk is (old_start, old lines, new lines) with no shared context."""
    out = [f"--- a/{path}", f"+++ b/{path}"]
    for start, old, new in hunks:
        out.append(f"@@ -{start},{len(old)} +{start},{len(new)} @@")
        out += [f"-{ln}" for ln in old] + [f"+{ln}" for ln in new]
    return "\n".join(out) + "\n"


def _target(root: Path, rel: str, lines: list[str], eol: str = "\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((eol.join(lines) + eol).encode("utf-8"))
    return path


def _laid_out_as_the_patch_expects(root: Path, file_patch) -> Path:
    """A file that has, at each hunk's line number, exactly the text the hunk removes."""
    lines: list[str] = []
    for hunk in file_patch.hunks:
        lines += [f"# filler {i}" for i in range(hunk.old_start - 1 - len(lines))]
        lines += hunk.old
    lines += ["# end"]
    return _target(root, file_patch.path, lines)


# ── the real patches ─────────────────────────────────────────────────────────────


def test_the_vendored_patches_parse():
    assert PATCHES, "no patch under patchspec/"
    for patch in PATCHES:
        files = ap.parse_patch(patch.read_text(encoding="utf-8"))
        assert files and all(f.hunks and f.path.endswith(".py") for f in files), patch.name
        for f in files:
            for h in f.hunks:
                assert h.old != h.new and h.old and h.new


@pytest.mark.parametrize("patch", PATCHES, ids=lambda p: p.name)
def test_a_real_patch_applies_and_then_is_a_no_op(tmp_path, patch):
    for file_patch in ap.parse_patch(patch.read_text(encoding="utf-8")):
        _laid_out_as_the_patch_expects(tmp_path, file_patch)
    ok, message = ap._apply(patch, tmp_path)
    assert ok and message == "applied", message
    for file_patch in ap.parse_patch(patch.read_text(encoding="utf-8")):
        text = (tmp_path / file_patch.path).read_text(encoding="utf-8")
        for hunk in file_patch.hunks:
            assert "\n".join(hunk.new) in text
            assert "\n".join(hunk.old) not in text
    again = ap._apply(patch, tmp_path)
    assert again == (True, "already applied")


def test_main_applies_every_patch_with_no_git_on_the_machine(tmp_path, monkeypatch, capsys):
    site = tmp_path / "site-packages"
    for patch in PATCHES:
        for file_patch in ap.parse_patch(patch.read_text(encoding="utf-8")):
            _laid_out_as_the_patch_expects(site, file_patch)
    monkeypatch.setattr(ap, "_site_packages", lambda: site)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))  # no git, no patch(1)

    def no_processes(*args, **kwargs):
        raise AssertionError(f"the patcher started a process: {args}")

    monkeypatch.setattr(subprocess, "run", no_processes)
    monkeypatch.setattr(subprocess, "Popen", no_processes)
    assert ap.main() == 0
    out = capsys.readouterr().out
    assert out.count("[OK]") == len(PATCHES) and "[FAIL]" not in out


def test_the_script_has_no_use_for_git():
    source = (BACKEND / "scripts" / "apply_patches.py").read_text(encoding="utf-8")
    assert "subprocess" not in source and '"git"' not in source
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "apply_patches.py" in dockerfile  # still part of the build...
    assert not any("apt-get" in ln and "git" in ln for ln in dockerfile.splitlines())  # ...without an apt layer for git


# ── how it behaves ───────────────────────────────────────────────────────────────


def test_a_file_that_drifted_by_some_lines_still_matches(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text(_diff("pkg/mod.py", (10, ["old = 1"], ["new = 1"])), encoding="utf-8")
    lines = [f"line {i}" for i in range(30)]
    lines[16] = "old = 1"  # the patch says line 10; a new release put it at 17
    target = _target(tmp_path, "pkg/mod.py", lines)
    assert ap._apply(patch, tmp_path) == (True, "applied")
    assert "new = 1" in target.read_text(encoding="utf-8") and "old = 1" not in target.read_text(encoding="utf-8")


def test_a_library_that_changed_under_the_patch_fails_and_is_left_alone(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text(_diff("pkg/mod.py", (3, ["old = 1"], ["new = 1"])), encoding="utf-8")
    target = _target(tmp_path, "pkg/mod.py", ["a", "b", "old = 2  # the library moved on", "c"])
    before = target.read_bytes()
    ok, message = ap._apply(patch, tmp_path)
    assert not ok and "matches neither the original nor the patched text" in message
    assert target.read_bytes() == before


def test_a_failure_in_the_second_file_changes_neither(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text(
        _diff("pkg/one.py", (1, ["old"], ["new"])) + _diff("pkg/two.py", (1, ["old"], ["new"])), encoding="utf-8"
    )
    one = _target(tmp_path, "pkg/one.py", ["old", "x"])
    _target(tmp_path, "pkg/two.py", ["something else", "x"])
    before = one.read_bytes()
    ok, _ = ap._apply(patch, tmp_path)
    assert not ok and one.read_bytes() == before, "a half-applied patch is worse than none"


def test_a_partly_applied_file_gets_the_rest(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text(_diff("pkg/mod.py", (1, ["a1"], ["a2"]), (5, ["b1"], ["b2"])), encoding="utf-8")
    target = _target(tmp_path, "pkg/mod.py", ["a2", "x", "y", "z", "b1"])  # the first hunk is already in
    ok, message = ap._apply(patch, tmp_path)
    assert ok and "1 hunk(s) were already in place" in message
    assert target.read_text(encoding="utf-8").splitlines() == ["a2", "x", "y", "z", "b2"]


def test_line_endings_and_the_final_newline_are_kept(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text(_diff("pkg/mod.py", (2, ["old"], ["new one", "new two"])), encoding="utf-8")
    target = _target(tmp_path, "pkg/mod.py", ["a", "old", "b"], eol="\r\n")
    assert ap._apply(patch, tmp_path)[0]
    assert target.read_bytes() == b"a\r\nnew one\r\nnew two\r\nb\r\n"


def test_a_file_without_a_final_newline_stays_that_way(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text(_diff("pkg/mod.py", (1, ["old"], ["new"])), encoding="utf-8")
    target = tmp_path / "pkg" / "mod.py"
    target.parent.mkdir()
    target.write_bytes(b"old")
    assert ap._apply(patch, tmp_path)[0] and target.read_bytes() == b"new"


def test_a_missing_target_is_reported_not_raised(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text(_diff("pkg/absent.py", (1, ["old"], ["new"])), encoding="utf-8")
    ok, message = ap._apply(patch, tmp_path)
    assert not ok and "absent.py" in message


def test_creating_a_file_is_refused_loudly(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text("--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,1 @@\n+print('x')\n", encoding="utf-8")
    ok, message = ap._apply(patch, tmp_path)
    assert not ok and "not supported" in message


def test_a_removed_line_that_looks_like_a_header_is_content(tmp_path):
    """``-- comment`` removed becomes ``--- comment`` in the diff; the hunk's own line counts keep it from being read as a file header."""
    patch = tmp_path / "x.patch"
    patch.write_text("--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,2 +1,2 @@\n--- comment\n+-- remark\n keep\n", encoding="utf-8")
    target = _target(tmp_path, "pkg/mod.py", ["-- comment", "keep"])
    assert ap._apply(patch, tmp_path)[0]
    assert target.read_text(encoding="utf-8").splitlines() == ["-- remark", "keep"]


def test_context_lines_must_match_too(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text("--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1,3 +1,3 @@\n before\n-old\n+new\n after\n", encoding="utf-8")
    good = _target(tmp_path, "pkg/mod.py", ["before", "old", "after"])
    assert ap._apply(patch, tmp_path)[0] and good.read_text(encoding="utf-8").splitlines() == ["before", "new", "after"]
    other = tmp_path / "other"
    bad = _target(other, "pkg/mod.py", ["BEFORE", "old", "after"])
    assert not ap._apply(patch, other)[0] and bad.read_text(encoding="utf-8").splitlines() == ["BEFORE", "old", "after"]
