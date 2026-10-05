"""Source-level warnings that become errors in newer Pythons must not creep in (round-4).

An invalid escape sequence in a plain string (``"\\s"`` in a docstring or a regex written without ``r``) is a
``DeprecationWarning`` on 3.11 — which is why it only showed up as one line in a warnings summary — a
``SyntaxWarning`` on 3.12 and 3.13, and slated to become a ``SyntaxError``. The installer picks whichever Python is
newest, so the code has to compile cleanly on all of them: this compiles every module with warnings as errors.
"""
from __future__ import annotations

import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_python_file_compiles_without_warnings():
    offenders = []
    for folder in ("app", "tests", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                try:
                    compile(path.read_text(encoding="utf-8"), str(path), "exec")
                except (SyntaxError, SyntaxWarning, DeprecationWarning) as exc:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}: {str(exc)[:100]}")
    assert not offenders, "\n".join(offenders)
