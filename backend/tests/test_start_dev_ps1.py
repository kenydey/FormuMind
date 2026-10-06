"""``scripts/windows/start-dev.ps1`` under a real PowerShell parser (round-5).

The runner wrote the worker's and Vite's PID files to ``$Pids.worker`` / ``$Pids.vite``. ``$Pids`` is the *directory* (a
string); the hashtable of file paths is ``$PidFiles``. PowerShell answers ``$null`` for a property a string does not
have, and ``Out-File -FilePath $null`` is a terminating error under ``$ErrorActionPreference = 'Stop'``: on Windows
``start`` brought the API up, launched the worker, and then died - no PID recorded for the worker (so ``stop`` could
never stop it), and the frontend never started. No grep for the file names finds that; it takes the syntax tree.

Runs wherever ``pwsh`` is installed (GitHub's ubuntu runners have it); the end-to-end proof is the ``windows-stack`` CI job.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "windows" / "start-dev.ps1"
PWSH = shutil.which("pwsh")

pytestmark = [
    pytest.mark.skipif(PWSH is None, reason="PowerShell (pwsh) not installed"),
    pytest.mark.skipif(not RUNNER.is_file(), reason="the Windows runner is not present"),
]

# Variables assigned from Join-Path / Split-Path hold a path *string*: a member access on one is a typo for the hashtable or
# object that was meant (a string has no ``.worker``) and evaluates to $null without a word.
_SCAN = r"""
param([string]$Path)
$errs = $null; $tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errs)
"PARSE_ERRORS=$($errs.Count)"
$errs | ForEach-Object { "line $($_.Extent.StartLineNumber): $($_.Message)" }
# PowerShell variable names are case-insensitive: a type held in $A would be clobbered by a loop variable $a.
$tAssign = [System.Management.Automation.Language.AssignmentStatementAst]
$tVar = [System.Management.Automation.Language.VariableExpressionAst]
$tCmd = [System.Management.Automation.Language.CommandAst]
$tMember = [System.Management.Automation.Language.MemberExpressionAst]
$tPipe = [System.Management.Automation.Language.PipelineAst]
$paths = @{}
foreach ($asg in $ast.FindAll({ param($n) $n -is $tAssign }, $true)) {
  if ($asg.Left -isnot $tVar -or $asg.Right -isnot $tPipe) { continue }
  # the assignment *is* a Join-Path / Split-Path call (not a hashtable that merely contains one)
  $parts = $asg.Right.PipelineElements
  if ($parts.Count -eq 1 -and $parts[0] -is $tCmd -and $parts[0].GetCommandName() -in 'Join-Path', 'Split-Path') {
    $paths[$asg.Left.VariablePath.UserPath] = $true
  }
}
"PATH_VARIABLES=$(($paths.Keys | Sort-Object) -join ',')"
foreach ($mem in $ast.FindAll({ param($n) $n -is $tMember }, $true)) {
  if ($mem.Expression -is $tVar -and $paths.ContainsKey($mem.Expression.VariablePath.UserPath)) {
    "MEMBER_ON_PATH line $($mem.Extent.StartLineNumber): $($mem.Extent.Text)"
  }
}
"DONE"
"""


def _scan(script: Path, tmp_path: Path) -> list[str]:
    scanner = tmp_path / "scan.ps1"
    scanner.write_text(_SCAN, encoding="utf-8")
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(scanner), "-Path", str(script)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1", "POWERSHELL_TELEMETRY_OPTOUT": "1"},
    )
    lines = proc.stdout.splitlines()
    assert lines and lines[-1] == "DONE", proc.stdout + proc.stderr
    return lines


def test_the_runner_parses(tmp_path):
    assert "PARSE_ERRORS=0" in _scan(RUNNER, tmp_path)


def test_no_member_is_read_off_a_path_string(tmp_path):
    lines = _scan(RUNNER, tmp_path)
    found = [ln for ln in lines if ln.startswith("MEMBER_ON_PATH")]
    assert not found, found
    # not vacuous: the scan did see the path variables it exists for
    variables = next(ln for ln in lines if ln.startswith("PATH_VARIABLES=")).split("=", 1)[1].split(",")
    assert {"Pids", "Logs", "Root", "VenvPy"} <= set(variables), variables


def test_the_scan_catches_the_original_mistake(tmp_path):
    broken = tmp_path / "broken.ps1"
    broken.write_text(
        "$Logs = 'logs'\n$Pids = Join-Path $Logs 'pids'\n$PidFiles = @{ worker = Join-Path $Pids 'worker.pid' }\n"
        "$proc.Id | Out-File -FilePath $Pids.worker -Encoding ascii\n",
        encoding="utf-8",
    )
    found = [ln for ln in _scan(broken, tmp_path) if ln.startswith("MEMBER_ON_PATH")]
    assert found == ["MEMBER_ON_PATH line 4: $Pids.worker"]


def test_each_process_the_runner_starts_records_its_pid_in_the_file_stop_reads():
    """The grep that does hold: the ``Out-File`` targets are exactly the entries ``Get-RecordedProcess`` looks up."""
    text = RUNNER.read_text(encoding="utf-8")
    written = set(re.findall(r"Out-File -FilePath \$PidFiles\.(\w+)", text))
    declared = set(re.findall(r"^\s+(\w+)\s*=\s*Join-Path \$Pids '\1\.pid'", text, re.MULTILINE))
    looked_up = set(re.findall(r"foreach \(\$kind in @\(([^)]*)\)\)", text)[0].replace("'", "").replace(" ", "").split(","))
    assert written == declared == {"api", "worker", "vite"}, (written, declared)
    assert looked_up == {"worker", "api", "vite"}


def test_worker_readiness_is_read_from_the_log_celery_writes_it_to():
    """Celery's ``ready.`` line goes to stderr (checked against a real worker: stdout 0 matches, stderr 1). The runner looked
    only at the stdout log, so every start waited its full 60 s and then warned about a healthy worker."""
    text = RUNNER.read_text(encoding="utf-8")
    block = text[text.index("function Start-Worker"): text.index("function Start-Frontend")]
    assert "$err" in block.split("Wait-For", 1)[1], "the readiness check does not look at the stderr log"
