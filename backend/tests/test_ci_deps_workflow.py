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


def _matrix(workflow: str) -> dict[str, str]:
    """extra -> its allow-list, from the job's matrix (parsed, not grepped: there are several ``allow:`` lines)."""
    import yaml

    include = yaml.safe_load(workflow)["jobs"]["extras"]["strategy"]["matrix"]["include"]
    return {row["extra"]: row["allow"] for row in include}


def test_no_extra_is_waived(workflow: str) -> None:
    """Every extra must resolve with every pin held. There were two waivers: `intel` (patent-client dragged httpx and
    pypdf down, and the Dockerfile installs `intel`, so the production image shipped pypdf 4.3.1) and, once the SDK moved
    out, a `patents` extra recording httpx 0.27.2 / pypdf 4.3.1. The SDK is gone altogether (EPO OPS is called over
    httpx), so the matrix has no `patents` row and no waiver: one added now is a downgrade shipped on purpose."""
    allow = _matrix(workflow)
    assert "patents" not in allow, "the patents extra no longer exists"
    assert allow["intel"] == "", allow["intel"]
    waived = {name: value for name, value in allow.items() if value}
    assert not waived, waived


def test_the_dockerfile_combination_is_resolved_against_the_pins(workflow: str) -> None:
    """One extra at a time cannot see two extras that disagree (file_ingest wanted pypdf>=6.19 while patent-client
    wanted pypdf<5), and the image is not built in CI - so the Dockerfile's whole list is resolved with the pins as
    constraints, and a change to the Dockerfile re-runs the gate."""
    import yaml

    doc = yaml.safe_load(workflow)
    steps = doc["jobs"]["dockerfile-extras"]["steps"]
    assert any("scripts/check_docker_extras.py" in step.get("run", "") for step in steps)
    triggers = doc.get("on") or doc.get(True)  # YAML 1.1 reads the bare key `on` as True
    for event in ("push", "pull_request"):
        assert "backend/Dockerfile" in triggers[event]["paths"], event
        assert "scripts/check_docker_extras.py" in triggers[event]["paths"], event
