"""The Windows installer and runner must stay in step with pyproject and with Windows.

Two failure modes are cheap to prevent here and expensive to hit on a lab machine:

* **An extras list naming something pyproject does not declare** (or a stray space such
  as ``notebookl m``). ``pip install -e ".[…]"`` resolves every extra in a single
  transaction, so one bad name aborts the whole command with ``Expected comma between
  extra names`` and *nothing* is installed — the operator is left with a venv that looks
  empty. The same transaction is why the installer is tiered at all: ColBERT
  (``ragatouille`` → ``voyager``, Windows wheels up to CPython 3.12 only) has to be
  separable from everything else.
* **POSIX-only syntax and Windows-hostile flags**: ``source .venv/bin/activate``,
  an inline ``VAR=value cmd`` prefix, ``celery`` without ``--pool=solo`` (the prefork
  pool cannot run on Windows), and a bare ``--reload-exclude .venv\\*`` that PowerShell
  expands into real paths before uvicorn sees them
  (``Got unexpected extra arguments (.venv\\Lib .venv\\pyvenv.cfg .venv\\Scripts)``).

These tests read the scripts as text. They do not execute PowerShell — they pin the
invariants that broke, not the formatting. Native-command exit-code checking and the
UTF-8 BOM of ``install.ps1`` are pinned by ``tests/test_installers.py``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the 3.10 floor only
    tomllib = pytest.importorskip("tomli")

REPO = Path(__file__).resolve().parents[2]
WINDOWS_DIR = REPO / "scripts" / "windows"
PYPROJECT = REPO / "backend" / "pyproject.toml"
INSTALL = REPO / "install.ps1"
INSTALL_BAT = REPO / "install.bat"
START = WINDOWS_DIR / "start-dev.ps1"

# Functions that relax $ErrorActionPreference around a native call whose stderr is
# ordinary output (progress, warnings) rather than a failure.
HARDENING = ("Invoke-Streamed", "Invoke-Probe", "Invoke-Optional")

pytestmark = pytest.mark.skipif(
    not INSTALL.is_file() or not START.is_file() or not PYPROJECT.is_file(),
    reason="Windows scripts not present (backend-only checkout)",
)


def _declared_extras() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return set(data["project"]["optional-dependencies"])


def _ps_array(text: str, name: str) -> list[str]:
    """The literal string entries of a PowerShell ``@("a","b")`` assignment."""
    match = re.search(rf"\${re.escape(name)}\s*=\s*@\((.*?)\)", text, re.S)
    assert match, f"${name} array not found"
    return re.findall(r"""['"]([^'"]+)['"]""", match.group(1))


def _code_lines(text: str) -> list[str]:
    """The script's lines with comments removed — what runs, not what it says.

    Strips PowerShell block comments (``<# … #>``, the help header) and ``#`` line
    comments, both of which legitimately *describe* the flags we refuse to execute.
    """
    without_blocks = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return [
        line for line in without_blocks.splitlines() if not line.lstrip().startswith("#")
    ]


def _hardened_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Line ranges of the helper functions that relax the error preference.

    The helpers end with ``}`` in the first column, which is how every function in
    these scripts is formatted.
    """
    spans = []
    for i, line in enumerate(lines):
        if line.startswith("function ") and any(h in line for h in HARDENING):
            for j in range(i + 1, len(lines)):
                if lines[j] == "}":
                    spans.append((i, j))
                    break
    return spans


def test_installer_extra_names_are_all_declared():
    declared = _declared_extras()
    text = INSTALL.read_text(encoding="utf-8-sig")

    named = _ps_array(text, "extras") + _ps_array(text, "torchExtras")
    # colbert is appended conditionally rather than listed literally.
    if re.search(r"""\$\w+\s*\+=\s*['"]colbert['"]""", text):
        named.append("colbert")
    # the core tier installs these two by name
    named += re.findall(r"\.\[(\w+),(\w+)\]", text)[0] if re.findall(r"\.\[(\w+),(\w+)\]", text) else []

    unknown = sorted({n for n in named if n} - declared)
    assert not unknown, f"install.ps1 names extras pyproject does not declare: {unknown}"


def test_no_whitespace_inside_a_pip_extras_spec():
    """`.[llm,science]` is a single token. A space (or a wrapped line) makes pip parse
    the tail as a second requirement — the exact typo that produced
    ``Expected comma between extra names``."""
    text = INSTALL.read_text(encoding="utf-8-sig")
    # Literal specs only: the built one ( `".[" + ($extras -join ",") + "]"` ) is code,
    # not a spec, so it is skipped by requiring the bracket body to hold no quotes.
    literals = re.findall(r"\.\[[^\]'\"]*\]", text)
    assert literals, "no literal extras spec found in install.ps1"
    for spec in literals:
        assert not re.search(r"\s", spec), f"whitespace inside extras spec: {spec!r}"


def test_colbert_is_gated_on_the_wheel_ceiling():
    """ragatouille -> voyager has Windows wheels only up to CPython 3.12, so the torch
    tier must add colbert conditionally and say so otherwise."""
    text = INSTALL.read_text(encoding="utf-8-sig")
    assert re.search(r"\$colbert\w*\s*=.*-le\s*12", text), "colbert gate must compare against 12"
    assert re.search(r"""if\s*\(\$\w+\)\s*\{[^}]*colbert""", text), "colbert must be conditional"
    assert "-Full" in text, "the torch tier must be opt-in"
    assert "-Minimal" in text, "the core-only path must stay available"


def test_the_probe_helper_survives_a_missing_command():
    """`& 不存在的命令` is a *terminating* error in PowerShell — `$ErrorActionPreference =
    'Continue'` does not downgrade it — so a probe that only relaxes the preference still
    aborts the install. That path is reachable: `py` exists while `py -3.12` may not.
    """
    text = INSTALL.read_text(encoding="utf-8-sig")
    body = text[text.index("function Invoke-Probe"):]
    body = body[: body.index("\nfunction ", 1)]
    assert "} catch {" in body, "Invoke-Probe must catch the command-not-found termination"
    assert re.search(r"Ok\s*=\s*\$false", body) or re.search(r"Ok\s*=\s*\$False", body), (
        "the catch arm must report a failed probe"
    )


def test_the_installer_rebuilds_a_venv_it_cannot_use():
    """A venv from another OS has no Scripts\\python.exe, and one from another CPython
    minor has the wrong wheel tags. Reusing either fails deep inside pip; the installer
    has to notice first and keep the old directory as .venv.bak-<stamp>."""
    text = INSTALL.read_text(encoding="utf-8-sig")
    assert "Test-Path $VenvPython" in text, "must detect a venv without Scripts\\python.exe"
    assert re.search(r"Rename-Item", text), "the unusable venv is renamed, not deleted"
    assert re.search(r"\.bak-", text), "the backup keeps a timestamp"
    assert "sys.version_info" in text, "must compare the existing venv's interpreter"


def test_celery_worker_uses_the_solo_pool():
    text = START.read_text(encoding="utf-8")
    assert "--pool=solo" in text, "Celery's prefork pool cannot run on Windows"


def test_uvicorn_watches_the_app_dir_instead_of_globbing_the_venv():
    code = "\n".join(_code_lines(START.read_text(encoding="utf-8")))
    assert "--reload-dir" in code and "'app'" in code, "watch app/ only"
    assert "--reload-exclude" not in code, (
        "PowerShell expands a bare .venv\\* into a path list and uvicorn treats it as "
        "positional arguments; use --reload-dir app"
    )


@pytest.mark.parametrize("script", [INSTALL, START], ids=lambda p: p.name)
def test_no_posix_activation_or_env_prefix_syntax(script):
    text = script.read_text(encoding="utf-8-sig")
    assert not re.search(r"(?m)^\s*source\s+\S", text), "`source` is bash-only"
    assert not re.search(r"(?m)^\s*FORMUMIND_[A-Z0-9_]+\s*=", text), (
        "`VAR=value cmd` is bash-only; use $env:VAR='value'"
    )


@pytest.mark.parametrize("script", [INSTALL, START], ids=lambda p: p.name)
def test_stderr_merging_native_calls_relax_the_error_preference(script):
    """`docker compose`, `alembic`, `pip` and `taskkill` print ordinary progress and
    warnings to stderr. Under `$ErrorActionPreference = 'Stop'`, merging that stream with
    `2>&1` turns those lines into ErrorRecords and kills an otherwise healthy run, so
    every merged call must be inside one of the hardening helpers — or be a call site of
    one, which are single-line `Invoke-Streamed { … }` invocations.
    """
    lines = _code_lines(script.read_text(encoding="utf-8-sig"))
    spans = _hardened_spans(lines)

    orphans = []
    for n, line in enumerate(lines):
        if "2>&1" not in line:
            continue
        guarded = any(h in line for h in HARDENING) or any(lo <= n <= hi for lo, hi in spans)
        if not guarded:
            orphans.append(f"line {n + 1}: {line.strip()}")

    assert not orphans, (
        "stderr is merged without relaxing $ErrorActionPreference (wrap the call in "
        "Invoke-Streamed / Invoke-Probe / Invoke-Optional):\n  " + "\n  ".join(orphans)
    )


def test_the_dev_launcher_pins_an_absolute_sqlite_url():
    """A relative sqlite URL resolves against each process's CWD, so uvicorn and Celery
    would open different files (this bit the Linux stack too: loop_history landed in a
    different DB than the UI read)."""
    text = START.read_text(encoding="utf-8")
    assert "sqlite:///$rootSlash/data/formumind.db" in text
    assert "sqlite:///./" not in text, "relative sqlite URL is CWD-dependent"


def test_dev_launcher_degrades_when_the_eln_is_unreachable():
    """Product compose runs datalab + REQUIRED=true; a Windows dev box without an ELN
    must still start, so the launcher probes first and falls back to auto/sqlite."""
    text = START.read_text(encoding="utf-8")
    assert "Resolve-ElnMode" in text
    assert "$env:FORMUMIND_DATALAB_REQUIRED = 'false'" in text
    assert "-RequireEln" in text, "strict ELN mode stays available for product runs"


def test_install_bat_forwards_arguments_and_the_exit_code():
    """install.bat is what a double-click runs. It must pass switches through (-Minimal /
    -Full) and must not report success when the installer failed."""
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert "-ExecutionPolicy Bypass" in text
    assert "-NoProfile" in text
    assert "%~dp0" in text, "must resolve the sibling install.ps1 relative to itself"
    assert "%*" in text, "switches must reach install.ps1"
    assert "ERRORLEVEL" in text, "must propagate the PowerShell exit code"


@pytest.mark.parametrize(
    "wrapper",
    sorted(WINDOWS_DIR.glob("*.cmd")),
    ids=lambda p: p.name,
)
def test_cmd_wrappers_bypass_execution_policy(wrapper):
    """The .cmd shims exist so the runner works from cmd.exe / a double-click without
    touching the machine-wide execution policy."""
    text = wrapper.read_text(encoding="utf-8")
    assert "-ExecutionPolicy Bypass" in text
    assert "-NoProfile" in text
    assert "%~dp0" in text, "must resolve the sibling .ps1 relative to itself"
    assert "ERRORLEVEL" in text, "must propagate the PowerShell exit code"
