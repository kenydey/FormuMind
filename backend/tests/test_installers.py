"""The one-click installers must work on a machine that has only a fresh clone (round-4).

Three ways they did not:

* ``data/`` is git-ignored, so the default ``sqlite:///./data/formumind.db`` points into a directory a
  fresh clone does not have. The application's ``make_engine`` creates it; ``alembic upgrade head`` — the
  installers' last step — did not, and died with "unable to open database file".
* ``install.ps1`` is UTF-8 *without a BOM* and full of Chinese text and ✓/⚠/❌. Windows PowerShell 5.1 (what
  ``install.bat`` launches) reads such a file in the ANSI code page: on a Western locale the bytes of ``✓``
  (E2 9C 93) decode to ``âœ“`` — and ``“`` is a string delimiter to PowerShell, so the script does not parse.
* PowerShell does not stop on a failing *native* command: with ``$ErrorActionPreference = "Stop"`` a failed
  ``pip install`` / ``npm install`` / ``alembic upgrade`` still fell through to "安装完成".
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .alembic_helpers import run_upgrade

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not (REPO / "install.ps1").is_file(), reason="installers not present")


def test_alembic_upgrade_creates_the_database_directory(tmp_path, monkeypatch):
    db = tmp_path / "fresh-clone" / "data" / "formumind.db"
    assert not db.parent.exists()
    run_upgrade(f"sqlite:///{db}", monkeypatch)
    assert db.is_file()


def test_powershell_installer_declares_its_encoding():
    raw = (REPO / "install.ps1").read_bytes()
    if any(b > 0x7F for b in raw):
        assert raw.startswith(b"\xef\xbb\xbf"), (
            "install.ps1 contains non-ASCII text but no UTF-8 BOM — Windows PowerShell 5.1 will read it as ANSI "
            "and typographic quotes in the mangled bytes break the parse"
        )


def test_powershell_installer_checks_native_exit_codes():
    text = (REPO / "install.ps1").read_text(encoding="utf-8-sig")
    unchecked = [
        f"line {n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        # quoted text (messages that merely mention `npm install`) is not an invocation
        if re.search(r"&\s+(\$VenvPython|npm|\$py\.Exe)\b", re.sub(r'"[^"]*"', '""', line))
        and not re.search(r"Invoke-(Step|Optional)\b", line)
        and "function " not in line
    ]
    assert not unchecked, (
        "native commands whose failure would go unnoticed (wrap them in Invoke-Step / Invoke-Optional):\n  "
        + "\n  ".join(unchecked)
    )


def test_the_optional_helper_does_not_leak_command_output_into_its_result():
    """``Invoke-Optional`` is used as ``if (-not (Invoke-Optional { … }))``. Uncaptured output of the
    script block would join the return value (a non-empty array is always true) and the warning would
    never show — the output has to be sent to the host."""
    text = (REPO / "install.ps1").read_text(encoding="utf-8-sig")
    body = text[text.index("function Invoke-Optional"):]
    body = body[: body.index("\nfunction ", 1)]
    assert "| Out-Host" in body


def test_the_batch_launcher_is_ascii_and_returns_the_installers_exit_code():
    """``install.bat`` is what a Windows user double-clicks.

    * ``cmd.exe`` decodes a batch file with the OEM code page (GBK on a Chinese Windows), where the last byte
      of a UTF-8 character can be read as a lead byte and swallow the line break after it — keep it ASCII.
    * The PowerShell script's exit code has to reach the caller (CI, wrappers).
    * A double-click console closes with the script, taking the next-steps text (or the error) with it, so it
      must pause — but only then, or every terminal / CI run would block on a key press.
    """
    raw = (REPO / "install.bat").read_bytes()
    assert all(b < 0x80 for b in raw), "install.bat must stay ASCII"
    text = raw.decode("ascii")
    assert "install.ps1" in text
    assert re.search(r"exit\s+/b\s+%RC%", text), "the installer's exit code is not handed back"
    assert re.search(r"set\s+\"?RC=%ERRORLEVEL%", text), "the exit code must be captured right after powershell returns"
    assert "%cmdcmdline%" in text and re.search(r"&&\s*pause\b", text), "pause only for a double-click launch"


def test_script_line_endings_are_pinned():
    rules = {}
    for line in (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pattern, *attrs = line.split()
            rules[pattern] = attrs
    assert "eol=lf" in rules.get("*.sh", [])
    assert "eol=crlf" in rules.get("*.bat", [])
    assert "eol=crlf" in rules.get("*.ps1", [])


# Not on Windows: ``bash`` there is whichever the PATH finds first (Git Bash, or the WSL launcher that exits 1 when no
# distribution is installed — the Windows CI job failed all three of these that way). The shell scripts are for
# POSIX hosts and the Linux jobs check them; Windows has install.bat / install.ps1 (tested above and in CI).
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell scripts; checked on the Linux jobs")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("script", ["install.sh", "scripts/install.sh", "scripts/start_all.sh"])
def test_shell_installers_are_valid_bash(script):
    subprocess.run(["bash", "-n", str(REPO / script)], check=True, capture_output=True)


_REFERENCED = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.一-鿿-]+\.(?:sh|py|md|yml|example))\b")


@pytest.mark.parametrize("script", ["install.sh", "install.ps1", "scripts/install.sh"])
def test_files_the_installers_point_at_exist(script):
    text = (REPO / script).read_text(encoding="utf-8-sig")
    missing = []
    for name in sorted(set(_REFERENCED.findall(text))):
        name = name.removeprefix("ROOT/")  # "$ROOT/scripts/install.sh"
        if name.startswith((".venv", "frontend/node_modules")) or "*" in name:
            continue
        candidates = [REPO / name, REPO / "backend" / name, REPO / "scripts" / name, REPO / "backend" / "scripts" / name]
        if not any(c.exists() for c in candidates):
            missing.append(name)
    # `apply_patches.py` / `rapidocr-attribute-fix.md` live under backend/scripts (resolved above);
    # anything left over is a dangling reference.
    assert not missing, f"{script} mentions files that do not exist: {missing}"
