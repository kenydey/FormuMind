"""The dependency workflow's flags, asserted where they cannot be checked at run time.

`scripts/check_pins.py` compares an extra's resolution against a baseline
resolution of the same project with no extras. That subtraction is only valid if
both reports are *complete*, and completeness comes entirely from one pip flag:

    pip install --dry-run --ignore-installed --report … -e '.'

Without `--ignore-installed`, pip omits packages the environment already
satisfies. Measured in this sandbox: 1 package in the baseline report versus 51
with the flag. Every package missing from the baseline then looks like the extra
introduced it, and the check silently reverts to the noisy pin comparison that
failed all six jobs for reasons nobody could act on.

The script cannot detect this itself — a truncated baseline is indistinguishable
from a genuinely small project — so the flag is pinned here, on the file that
carries it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci-deps.yml"
)


@pytest.fixture(scope="module")
def workflow() -> str:
    if not _WORKFLOW.is_file():
        pytest.skip("ci-deps workflow not present")
    return _WORKFLOW.read_text(encoding="utf-8")


def _resolve_commands(text: str) -> list[str]:
    """Every `pip install --dry-run …` line, joined across backslash breaks."""
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [
        line.strip()
        for line in joined.splitlines()
        if "pip install" in line and "--dry-run" in line
    ]


def test_both_resolutions_ignore_what_is_already_installed(workflow: str) -> None:
    commands = _resolve_commands(workflow)
    assert len(commands) == 2, f"expected a baseline and an extra, got {commands}"
    for command in commands:
        assert "--ignore-installed" in command, (
            f"truncated report: {command}\n"
            "Without --ignore-installed the baseline omits satisfied packages "
            "and the comparison silently stops working."
        )


def test_one_resolution_is_the_extra_free_baseline(workflow: str) -> None:
    """The control arm has to actually be extra-free, or it subtracts nothing."""
    commands = _resolve_commands(workflow)
    assert any(re.search(r"-e\s+'\.'", c) for c in commands), commands
    assert any("[" in c and "matrix.extra" in c for c in commands), commands


def test_the_check_is_given_the_baseline(workflow: str) -> None:
    """Resolving a baseline and then not passing it would be worse than useless.

    The job would spend the time and still compare against the pins.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", workflow)
    # `check_pins.py` also appears under `paths:` as a trigger; only the line
    # that actually invokes it is the one with arguments to assert.
    check = [
        ln for ln in joined.splitlines()
        if "check_pins.py" in ln and "python" in ln
    ]
    assert check, "the workflow no longer runs the check"
    for line in check:
        assert "--baseline=" in line, line
        assert "--report=" in line, line


def test_the_known_downgrades_are_recorded_at_exact_versions(workflow: str) -> None:
    """`intel` really does downgrade four packages; that stays reviewed, not waived.

    Pinned to exact versions so the exception covers only the drift that was
    actually looked at — if it changes, the job fails again rather than the
    waiver quietly widening.
    """
    allow = re.search(r"allow:\s*\"([^\"]+)\"", workflow)
    assert allow, "the intel allow-list disappeared"
    entries = dict(item.split(":", 1) for item in allow.group(1).split(","))
    assert entries == {
        "arxiv": "3.0.0",
        "ddgs": "9.14.3",
        "httpx": "0.27.2",
        "pypdf": "4.3.1",
    }
    assert "allow: \"\"" not in allow.group(0)
