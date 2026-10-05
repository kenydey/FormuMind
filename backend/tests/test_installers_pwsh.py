"""``install.ps1`` under a real PowerShell parser (round-4).

``test_installers.py`` can only grep the script. PowerShell has two behaviours a grep cannot confirm and a
Linux developer never sees — whether the file *parses* once a Windows host has decoded it, and whether
``$LASTEXITCODE`` handling really stops on a failed native command — so these run the script's own helpers
through ``pwsh`` whenever it is installed (GitHub's ubuntu runners ship it; the tests skip elsewhere).

PowerShell 7 reads BOM-less files as UTF-8, so the original failure cannot be reproduced by just running the
script: instead the bytes are decoded the way Windows PowerShell 5.1 does (ANSI code page when there is no
BOM) and fed to the parser.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "install.ps1"
PWSH = shutil.which("pwsh")

pytestmark = [
    pytest.mark.skipif(PWSH is None, reason="PowerShell (pwsh) not installed"),
    pytest.mark.skipif(not INSTALLER.is_file(), reason="installers not present"),
]

_PARSE_ERRORS = r"""
param([string]$Path, [string]$Encoding)
if ($Encoding -eq 'cp1252') {
  [System.Text.Encoding]::RegisterProvider([System.Text.CodePagesEncodingProvider]::Instance)
}
$bytes = [System.IO.File]::ReadAllBytes($Path)
$hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
$body = if ($hasBom) { $bytes[3..($bytes.Length - 1)] } else { $bytes }
$enc = if ($Encoding -eq 'cp1252') { [System.Text.Encoding]::GetEncoding(1252) } else { [System.Text.Encoding]::UTF8 }
$errs = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseInput($enc.GetString($body), [ref]$tokens, [ref]$errs)
$errs | ForEach-Object { "line $($_.Extent.StartLineNumber): $($_.Message)" }
"ERRORS=$($errs.Count)"
"""


def _pwsh(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        env={**os.environ, "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1", "POWERSHELL_TELEMETRY_OPTOUT": "1"},
    )


def _parse_error_count(tmp_path: Path, encoding: str) -> tuple[int, str]:
    script = tmp_path / "parse.ps1"
    script.write_text(_PARSE_ERRORS, encoding="utf-8")
    out = _pwsh("-File", str(script), "-Path", str(INSTALLER), "-Encoding", encoding).stdout
    return int(out.rsplit("ERRORS=", 1)[1].split()[0]), out


def test_install_ps1_parses(tmp_path):
    count, out = _parse_error_count(tmp_path, "utf8")
    assert count == 0, out


def test_the_bom_is_what_keeps_it_parseable_on_an_ansi_host(tmp_path):
    """Without the BOM, Windows PowerShell 5.1 decodes the file as cp1252 and the script stops parsing —
    the failure the BOM prevents. If the script ever becomes pure ASCII this test has nothing left to show."""
    raw = INSTALLER.read_bytes()
    if not any(b > 0x7F for b in raw):
        pytest.skip("install.ps1 is ASCII-only: encoding no longer matters")
    assert raw.startswith(b"\xef\xbb\xbf")
    no_bom = tmp_path / "install-no-bom.ps1"
    no_bom.write_bytes(raw[3:])
    script = tmp_path / "parse.ps1"
    script.write_text(_PARSE_ERRORS, encoding="utf-8")
    out = _pwsh("-File", str(script), "-Path", str(no_bom), "-Encoding", "cp1252").stdout
    assert int(out.rsplit("ERRORS=", 1)[1].split()[0]) > 0, "a BOM-less install.ps1 now parses on an ANSI host — is the BOM still needed?"


def test_step_helpers_stop_on_a_failed_native_command(tmp_path):
    text = INSTALLER.read_text(encoding="utf-8-sig")
    helpers = text[text.index("function Write-Step"): text.index("function Find-Python")]
    probe = tmp_path / "helpers.ps1"
    probe.write_text(
        '$ErrorActionPreference = "Stop"\n'
        f'$native = "{PWSH}"\n'
        + helpers
        + r'''
Invoke-Step "ok-step" { & $native -NoProfile -Command "exit 0" }
"STEP_OK_CONTINUED"
$failed = Invoke-Optional { "noise"; & $native -NoProfile -Command "exit 1" }
"OPTIONAL_FAIL=[$failed]:$($failed.GetType().Name)"
$passed = Invoke-Optional { "noise"; & $native -NoProfile -Command "exit 0" }
"OPTIONAL_OK=[$passed]:$($passed.GetType().Name)"
$chatty = Invoke-Optional { & $native -NoProfile -Command "[Console]::Error.WriteLine('to stderr'); exit 0" }
"OPTIONAL_STDERR=[$chatty]"
Invoke-Step "failing-step" { & $native -NoProfile -Command "exit 3" }
"SHOULD_NOT_PRINT"
''',
        encoding="utf-8",
    )
    done = _pwsh("-File", str(probe))
    out = done.stdout
    assert "STEP_OK_CONTINUED" in out
    # the optional helper returns one boolean — its command's output must not join the return value
    assert "OPTIONAL_FAIL=[False]:Boolean" in out, out
    assert "OPTIONAL_OK=[True]:Boolean" in out, out
    assert "OPTIONAL_STDERR=[True]" in out, out  # stderr under $ErrorActionPreference=Stop must not be fatal
    # a failed required step ends the script with that exit code, and nothing after it runs
    assert "SHOULD_NOT_PRINT" not in out
    assert done.returncode == 3, (done.returncode, out, done.stderr)
    assert "failing-step" in out
