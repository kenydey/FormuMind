"""How ``install.bat`` behaves for each way a Windows user starts it (round-5; runs on Windows only).

``test_installers.py`` greps the launcher for ``%cmdcmdline% ... && pause``; that proves the line is there, not that it
decides correctly. The launcher exists for one case - a double-click opens a console that closes with the script, taking the
next steps (or the error) with it, so it must pause *then* - and must not pause in a terminal, where it would only block.

Nothing here runs the installer: ``install.ps1`` is replaced by a stub that echoes its arguments and exits with a chosen code,
and the ``pause`` is replaced by a line that leaves a flag file (a real ``pause`` would wait for a key nobody presses). What is
under test is everything else - the decision, the argument forwarding, the exit code - exactly as ``cmd.exe`` evaluates it.

An Explorer double-click starts ``cmd.exe /c ""<path>" "``; the form is built by hand because CI has no desktop to click on.
The ``windows-stack`` job also prints what ``%cmdcmdline%`` really is under the shell association, so the string used here can
be compared with the one Explorer produces.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe launch behaviour")

STUB = 'Write-Host "STUB-RAN:$($args -join \'|\')"\r\nexit ([int]$env:STUB_EXIT)\r\n'


@pytest.fixture()
def launcher(tmp_path: Path) -> Path:
    # a space in the path, like C:\Users\Zhang San\Desktop - the characters that make cmd's quoting rules matter
    folder = tmp_path / "Form Mind"
    folder.mkdir()
    original = (REPO / "install.bat").read_bytes().decode("ascii")
    assert original.count("&& pause") == 1, "the launcher no longer ends in `&& pause`"
    observed = original.replace("&& pause", '&& echo paused> "%~dp0paused.flag"')
    (folder / "install.bat").write_bytes(observed.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
    (folder / "install.ps1").write_bytes(STUB.encode("ascii"))
    return folder / "install.bat"


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


def test_a_double_click_pauses_so_the_console_does_not_close_on_the_result(launcher):
    result = _run(f'cmd.exe /c ""{launcher}" "', cwd=launcher.parent)
    assert "STUB-RAN:" in result.stdout, result.stdout + result.stderr
    assert _paused(launcher), "no pause: the console would close with the error / next steps in it"


def test_a_double_click_still_returns_the_installers_exit_code(launcher):
    result = _run(f'cmd.exe /c ""{launcher}" "', cwd=launcher.parent, exit_code=7)
    assert result.returncode == 7, result.stdout + result.stderr


def test_a_terminal_run_does_not_pause(launcher):
    """Typed at an interactive prompt: ``%cmdcmdline%`` is the prompt's own command line, not the script's."""
    typed = f'"{launcher}" -Minimal\r\nexit\r\n'
    result = _run("cmd.exe /q", cwd=launcher.parent, stdin_text=typed)
    assert "STUB-RAN:-Minimal" in result.stdout, result.stdout + result.stderr
    assert not _paused(launcher), "a terminal run was held up by a pause"


def test_arguments_reach_the_installer(launcher):
    result = _run(f'cmd.exe /c ""{launcher}" -Minimal -SkipFrontend"', cwd=launcher.parent)
    assert "STUB-RAN:-Minimal|-SkipFrontend" in result.stdout, result.stdout + result.stderr


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


def test_how_powershell_and_a_plain_cmd_c_are_treated_is_recorded(launcher):
    """Not an assertion about the right answer - there is none the batch file can know - but a record of what the two
    other common launches do, so a change in either shows up in the log of this job rather than in a user's console."""
    unquoted = _run(f"cmd.exe /c {launcher.name} -Minimal", cwd=launcher.parent)
    unquoted_paused = _paused(launcher)
    (launcher.parent / "paused.flag").unlink(missing_ok=True)
    from_powershell = _run(["powershell.exe", "-NoProfile", "-Command", f"& '{launcher}' -Minimal; exit $LASTEXITCODE"], cwd=launcher.parent)
    powershell_paused = _paused(launcher)
    print(f"\ncmd /c install.bat -Minimal        -> pause {'fires' if unquoted_paused else 'does not fire'} (rc {unquoted.returncode})")
    print(f"powershell: & install.bat -Minimal -> pause {'fires' if powershell_paused else 'does not fire'} (rc {from_powershell.returncode})")
    assert "STUB-RAN:-Minimal" in unquoted.stdout and "STUB-RAN:-Minimal" in from_powershell.stdout
