#!/usr/bin/env python3
"""Apply third-party library patches after pip install.

Location: backend/scripts/apply_patches.py
Run after `pip install -e ".[parse_pro,…]"` so that pymupdf4llm's RapidOCR
attribute-name bug is fixed before OCR ever runs.

Patches live in backend/patchspec/<pkg>/<name>.patch and are applied with
`git apply --directory=<site-packages>` semantics. Each patch is idempotent:
applying twice is a no-op (git apply skips already-applied hunks with a warning,
so a hunk that matches is verified rather than duplicated).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PATCHSPEC_DIR = Path(__file__).resolve().parent.parent / "patchspec"


def _site_packages() -> Path:
    import site

    return Path(site.getsitepackages()[0])


def _already_applied(patch: Path, root: Path) -> bool:
    """Whether the patch's post-state is already present.

    `git apply --check` is unreliable here (fuzzy matching returns 0 for both
    states). We parse the patch into per-file (hunk-grouped) additions: for the
    patch to count as applied, every `+` line of every file's hunks must be
    present in that specific file.
    """
    lines = patch.read_text().splitlines()
    current_target: str | None = None
    per_file_added: dict[str, list[str]] = {}
    for ln in lines:
        if ln.startswith("+++ b/"):
            current_target = ln[6:].split("\t")[0].removeprefix("b/").removeprefix("a/")
            per_file_added.setdefault(current_target, [])
        elif ln.startswith("+") and not ln.startswith("+++"):
            if current_target is not None:
                per_file_added[current_target].append(ln[1:])
    if not per_file_added:
        return False
    for tgt, added_lines in per_file_added.items():
        f = root / tgt
        if not f.exists():
            return False
        content = f.read_text()
        for m in added_lines:
            if m.strip() and m.strip() not in content:
                return False
    return True


def _apply(patch: Path, root: Path) -> tuple[bool, str]:
    """Apply one unified-diff patch. Returns (ok, message)."""
    if _already_applied(patch, root):
        return True, "already applied"
    # Strip the `a/` / `b/` prefixes with -p1 and run from inside site-packages.
    # (NOT `--directory`, which *prepends* a path instead of stripping, and then
    # fuzzy matching silently no-ops with exit 0 without touching the file.)
    # The patch path must be absolute because cwd changes to `root`.
    opt = ["git", "apply", "-p1", "--whitespace=nowarn", str(patch.resolve())]
    r = subprocess.run(opt, cwd=root, capture_output=True, text=True)
    if r.returncode == 0:
        # Verify post-state is now present (guard against a silent no-op).
        if _already_applied(patch, root):
            return True, "applied"
        return False, "git apply returned 0 but post-state not detected"
    return False, r.stderr.strip() or r.stdout.strip()


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