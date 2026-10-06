#!/usr/bin/env python3
"""Apply third-party library patches after pip install.

Location: backend/scripts/apply_patches.py
Run after `pip install -e ".[parse_pro,…]"` so that pymupdf4llm's RapidOCR
attribute-name bug is fixed before OCR ever runs.

Patches live in backend/patchspec/<pkg>/<name>.patch (unified diffs whose paths are relative to
site-packages, ``a/`` / ``b/`` prefixes) and are applied **in pure Python**.

This used to shell out to ``git apply``. ``python:3.11-slim`` has no git, so ``RUN python scripts/apply_patches.py`` died with
``FileNotFoundError: 'git'`` and the Docker image did not build at all - found the first time anything built it
(scripts/docker_smoke.sh). Adding git back would mean an apt layer, which the Dockerfile deliberately does not have.

Idempotent, decided hunk by hunk: a hunk whose *new* text is already in the file counts as applied; one whose *old* text is
there is applied; one that is neither is a failure (the library changed under the patch) and leaves the file untouched.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PATCHSPEC_DIR = Path(__file__).resolve().parent.parent / "patchspec"

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(Exception):
    """The patch cannot be read, or does not fit the file it targets."""


@dataclass
class Hunk:
    old_start: int  # 1-based line in the original file
    old: list[str] = field(default_factory=list)  # context + removed lines: what the file must contain there
    new: list[str] = field(default_factory=list)  # context + added lines: what it contains afterwards


@dataclass
class FilePatch:
    path: str  # relative to the root, `a/` / `b/` stripped (-p1)
    hunks: list[Hunk] = field(default_factory=list)


def _site_packages() -> Path:
    import site

    return Path(site.getsitepackages()[0])


def _strip_prefix(path: str) -> str:
    path = path.split("\t")[0].strip()
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def parse_patch(text: str) -> list[FilePatch]:
    """Parse a unified diff into per-file hunks. Only modifications of existing files are supported."""
    files: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    old_left = new_left = 0
    old_header = ""
    for raw in text.splitlines():
        if hunk is not None and (old_left > 0 or new_left > 0):
            if raw.startswith("\\"):  # "\ No newline at end of file"
                continue
            tag, body = (raw[:1], raw[1:]) if raw else (" ", "")  # an editor may have stripped a blank context line
            if tag == " ":
                hunk.old.append(body)
                hunk.new.append(body)
                old_left -= 1
                new_left -= 1
            elif tag == "-":
                hunk.old.append(body)
                old_left -= 1
            elif tag == "+":
                hunk.new.append(body)
                new_left -= 1
            else:
                raise PatchError(f"unexpected line inside a hunk: {raw!r}")
            continue
        if raw.startswith("--- "):
            old_header = raw[4:].split("\t")[0].strip()
            hunk = None
        elif raw.startswith("+++ "):
            new_header = raw[4:].split("\t")[0].strip()
            if "/dev/null" in (old_header, new_header):
                raise PatchError("creating or deleting a file is not supported")
            current = FilePatch(path=_strip_prefix(new_header))
            files.append(current)
            hunk = None
        elif raw.startswith("@@"):
            match = _HUNK_HEADER.match(raw)
            if match is None or current is None:
                raise PatchError(f"malformed hunk header: {raw!r}")
            hunk = Hunk(old_start=int(match.group(1)))
            old_left = int(match.group(2)) if match.group(2) is not None else 1
            new_left = int(match.group(4)) if match.group(4) is not None else 1
            current.hunks.append(hunk)
        # anything else between hunks (`diff --git`, `index ...`, blank lines) carries no content
    if not files or not any(f.hunks for f in files):
        raise PatchError("no hunks found")
    return files


def _locate(lines: list[str], block: list[str], expected: int) -> int | None:
    """Index where ``block`` sits in ``lines``, nearest to ``expected`` (exact text, trailing whitespace aside)."""
    if not block:
        return None
    wanted = [b.rstrip() for b in block]
    last = len(lines) - len(block)
    for distance in range(0, max(expected, last - expected, 0) + 1):
        for at in (expected - distance, expected + distance):
            if 0 <= at <= last and [ln.rstrip() for ln in lines[at: at + len(block)]] == wanted:
                return at
    return None


def apply_to_text(text: str, hunks: list[Hunk]) -> tuple[str, int, int]:
    """Apply ``hunks`` to ``text``. Returns (new text, hunks applied now, hunks already in place); raises PatchError."""
    eol = "\r\n" if "\r\n" in text else "\n"
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()
    offset = 0  # how far earlier hunks moved the lines the next one refers to
    applied = already = 0
    for hunk in hunks:
        expected = hunk.old_start - 1 + offset
        at = _locate(lines, hunk.old, expected)
        if at is not None:
            lines[at: at + len(hunk.old)] = hunk.new
            offset += (at - expected) + (len(hunk.new) - len(hunk.old))
            applied += 1
            continue
        if _locate(lines, hunk.new, expected) is not None:
            already += 1
            continue
        raise PatchError(f"hunk at line {hunk.old_start} matches neither the original nor the patched text")
    result = eol.join(lines)
    if ends_with_newline:
        result += eol
    return result, applied, already


def _apply(patch: Path, root: Path) -> tuple[bool, str]:
    """Apply one unified-diff patch under ``root``. Returns (ok, message); on failure no file is changed."""
    try:
        files = parse_patch(patch.read_text(encoding="utf-8"))
        rewritten: dict[Path, str] = {}
        applied = already = 0
        for file_patch in files:
            target = root / file_patch.path
            if not target.is_file():
                return False, f"{file_patch.path}: not found under {root}"
            text = target.read_bytes().decode("utf-8")
            new_text, now, before = apply_to_text(text, file_patch.hunks)
            applied += now
            already += before
            if new_text != text:
                rewritten[target] = new_text
    except (PatchError, UnicodeDecodeError, OSError) as exc:
        return False, str(exc)
    for target, new_text in rewritten.items():
        target.write_bytes(new_text.encode("utf-8"))  # bytes: no newline translation on Windows
    if applied == 0:
        return True, "already applied"
    return True, "applied" if already == 0 else f"applied ({already} hunk(s) were already in place)"


def main() -> int:
    if not PATCHSPEC_DIR.is_dir():
        print(f"patchspec dir not found: {PATCHSPEC_DIR}", file=sys.stderr)
        return 1
    sp = _site_packages()
    failures = 0
    for patch in sorted(PATCHSPEC_DIR.rglob("*.patch")):
        ok, msg = _apply(patch, sp)
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {patch.name}: {msg or 'applied'}")
        if not ok:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
