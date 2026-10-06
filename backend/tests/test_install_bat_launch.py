"""How ``install.bat`` behaves for each way a Windows user starts it (round-5; runs on Windows only).

``test_installers.py`` greps the launcher for its pause line; that proves the line is there, not that it decides correctly. The
launcher exists for one case - a double-click opens a console that closes with the script, taking the next steps (or the error)
with it, so it must wait for a key *then* - and must not wait in a terminal or in somebody's script, where it would only block.

It decides from ``%cmdcmdline%``, the command line cmd.exe itself was started with. What that is for each way of starting a
``.bat`` was measured on a Windows runner (the ``windows-stack`` job prints it on every run)::

    double-click, Start-Process with no arguments   C:\\Windows\\system32\\cmd.exe /c ""<dir>\\install.bat" "
    Start-Process with arguments, PowerShell call   C:\\Windows\\system32\\cmd.exe /c ""<dir>\\install.bat" -X"
    cmd /c "<dir>\\install.bat" -X                    "C:\\Windows\\system32\\cmd.exe" /c ""<dir>\\install.bat" -X"
    typed at a prompt                               "C:\\Windows\\system32\\cmd.exe" /q

Only the first ends in quote, space, quote - the shell association is ``"%1" %*`` and ``%*`` is empty - so that is what pauses.
The older test (``echo %cmdcmdline% | find "install.bat"``) paused for all but the last, which held up every PowerShell and
``cmd /c`` caller, and broke on a path containing ``&`` or ``)``.

Nothing here runs the installer: ``install.ps1`` is replaced by a stub that records its arguments and exits with a chosen code,
and the ``pause`` is replaced by a line that leaves a flag file (a real ``pause`` would wait for a key nobody presses). What is
under test is everything else - the decision, the argument forwarding, the exit code - exactly as ``cmd.exe`` evaluates it.
The real shell association is exercised too (``Start-Process`` on the file), which is the call Explorer makes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe launch behaviour")

STUB = (
    "Set-Content -Path (Join-Path $PSScriptRoot 'ran.txt') -Value ($args -join '|') -Encoding ASCII\r\n"
    'Write-Host "STUB-RAN:$($args -join \'|\')"\r\n'
    "exit ([int]$env:STUB_EXIT)\r\n"
)
PAUSE = 'if "%CL:~-3%"=="Q Q" pause'
# a space (C:\Users\Zhang San\Desktop), cmd's own metacharacters, and Chinese - what a real desktop path can look like
FOLDERS = ["Form Mind", "Form & Mind (x86)", "张三 桌面"]


def _launcher(root: Path, folder: str) -> Path:
    where = root / folder
    where.mkdir()
    original = (REPO / "install.bat").read_bytes().decode("ascii")
    assert original.count(PAUSE) == 1, f"the launcher's pause line is no longer `{PAUSE}`"
    observed = original.replace(PAUSE, PAUSE.replace(" pause", ' echo paused> "%~dp0paused.flag"'))
    (where / "install.bat").write_bytes(observed.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
    (where / "install.ps1").write_bytes(STUB.encode("ascii"))
    return where / "install.bat"


@pytest.fixture()
def launcher(tmp_path: Path) -> Path:
    return _launcher(tmp_path, FOLDERS[0])


@pytest.fixture(params=FOLDERS, ids=["space", "metacharacters", "chinese"])
def any_launcher(request, tmp_path: Path) -> Path:
    return _launcher(tmp_path, request.param)


def _run(command, *, cwd: Path, exit_code: int = 0, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """``command`` as a raw command line (a str is passed to CreateProcess as it is, quotes and all)."""
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=180,
        input=stdin_text if stdin_text is not None else None,
        stdin=None if stdin_text is not None else subprocess.DEVNULL,
        env={**os.environ, "STUB_EXIT": str(exit_code)},
    )


def _paused(bat: Path) -> bool:
    return (bat.parent / "paused.flag").exists()


def _ran_with(bat: Path) -> str | None:
    """The arguments the stub received, or None when it never ran (``Start-Process`` opens its own console: stdout is not ours)."""
    ran = bat.parent / "ran.txt"
    return ran.read_text(encoding="ascii").strip() if ran.exists() else None


def _powershell(command: str, *, cwd: Path, exit_code: int = 0) -> subprocess.CompletedProcess[str]:
    return _run(["powershell.exe", "-NoProfile", "-Command", command], cwd=cwd, exit_code=exit_code)


# ── a double-click ────────────────────────────────────────────────────────────────────────────────


def test_a_double_click_pauses_so_the_console_does_not_close_on_the_result(launcher):
    result = _run(f'cmd.exe /c ""{launcher}" "', cwd=launcher.parent)
    assert "STUB-RAN:" in result.stdout, result.stdout + result.stderr
    assert _paused(launcher), "no pause: the console would close with the error / next steps in it"


def test_the_real_shell_association_is_a_double_click_too(launcher):
    """``Start-Process`` on the file is the call Explorer makes; no arguments, so the association's ``%*`` is empty."""
    result = _powershell(f"Start-Process -FilePath '{launcher}' -Wait", cwd=launcher.parent)
    assert _ran_with(launcher) == "", result.stdout + result.stderr
    assert _paused(launcher), "the shell-association launch did not pause"


def test_a_double_click_pauses_whatever_the_path_looks_like(any_launcher):
    """The old test ran the command line through a pipe, which a ``&`` or ``)`` in the folder name broke."""
    result = _run(f'cmd.exe /c ""{any_launcher}" "', cwd=any_launcher.parent)
    assert _ran_with(any_launcher) == "", result.stdout + result.stderr
    assert _paused(any_launcher)


def test_a_double_click_still_returns_the_installers_exit_code(launcher):
    result = _run(f'cmd.exe /c ""{launcher}" "', cwd=launcher.parent, exit_code=7)
    assert result.returncode == 7, result.stdout + result.stderr
    assert _paused(launcher), "a failed install must hold the console too: the error is the thing the user has to read"


# ── everything that is not a double-click ─────────────────────────────────────────────────────────


def test_a_terminal_run_does_not_pause(launcher):
    """Typed at an interactive prompt: ``%cmdcmdline%`` is the prompt's own command line, not the script's."""
    typed = f'"{launcher}" -Minimal\r\nexit\r\n'
    result = _run("cmd.exe /q", cwd=launcher.parent, stdin_text=typed)
    assert "STUB-RAN:-Minimal" in result.stdout, result.stdout + result.stderr
    assert not _paused(launcher), "a terminal run was held up by a pause"


def test_arguments_reach_the_installer_and_a_launch_with_arguments_does_not_pause(any_launcher):
    result = _run(f'cmd.exe /c ""{any_launcher}" -Minimal -SkipFrontend"', cwd=any_launcher.parent)
    assert _ran_with(any_launcher) == "-Minimal|-SkipFrontend", result.stdout + result.stderr
    assert not _paused(any_launcher)


def test_a_failing_installer_fails_the_launcher(launcher):
    result = _run(f'cmd.exe /c ""{launcher}" -Minimal"', cwd=launcher.parent, exit_code=3)
    assert result.returncode == 3, result.stdout + result.stderr


def test_a_script_that_calls_it_gets_the_exit_code_and_no_pause(launcher):
    """What CI and other tooling do (``call install.bat``): the batch file runs inside *their* cmd.exe, whose command line
    never names it, so nothing pauses - and the exit code must come through ``call``."""
    script = launcher.parent / "caller.cmd"
    script.write_bytes(f'@echo off\r\ncall "{launcher}" -Minimal\r\nexit /b %ERRORLEVEL%\r\n'.encode("ascii"))
    result = _run(f'cmd.exe /c ""{script}""', cwd=launcher.parent, exit_code=5)
    assert result.returncode == 5, result.stdout + result.stderr
    assert not _paused(launcher)


@pytest.mark.parametrize(
    "form",
    [
        pytest.param(lambda bat: f"cmd.exe /c {bat.name} -Minimal", id="cmd-c-relative-with-argument"),
        pytest.param(lambda bat: f'cmd.exe /c "{bat}"', id="cmd-c-quoted-path-only"),
        pytest.param(lambda bat: ["powershell.exe", "-NoProfile", "-Command", f"& '{bat}' -Minimal; exit $LASTEXITCODE"], id="powershell-call-with-argument"),
        pytest.param(lambda bat: ["powershell.exe", "-NoProfile", "-Command", f"& '{bat}'; exit $LASTEXITCODE"], id="powershell-call-no-arguments"),
        pytest.param(lambda bat: ["powershell.exe", "-NoProfile", "-Command", f"Start-Process -FilePath '{bat}' -ArgumentList '-Minimal' -Wait"], id="start-process-with-argument"),
    ],
)
def test_a_scripted_start_never_pauses(launcher, form):
    """Not a double-click, so nobody is there to press a key: the CI step that runs this would hang until its timeout.
    (The old test paused for the first four of these - they all name the file on the command line.)"""
    result = _run(form(launcher), cwd=launcher.parent)
    assert _ran_with(launcher) is not None, f"the launch itself failed: rc={result.returncode}\n{result.stdout}\n{result.stderr}"
    assert not _paused(launcher), "a scripted start was held up by a pause"
