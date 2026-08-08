"""The dependency gate itself, because it was wrong and nothing caught it.

`scripts/check_pins.py` guards a real failure mode — an optional extra quietly
downgrading a pinned base dependency — but it ran for weeks failing on every
extra for a reason that had nothing to do with extras: `pip install --dry-run
--report` does not apply requirements.txt, so pyproject's `>=` bounds resolve to
the newest release and four packages looked "upgraded" in all six jobs.

A gate that is red for a reason nobody can act on is a gate everybody learns to
scroll past, so these tests pin both halves: the noise must not fail, and the
signal must still fail.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pins.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_pins", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_pins = pytest.importorskip("importlib") and _load()


def _report(tmp_path: Path, name: str, packages: dict[str, str]) -> Path:
    """A pip `--report` file, in the shape pip actually emits."""
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {"install": [
                {"metadata": {"name": n, "version": v}} for n, v in packages.items()
            ]}
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def requirements(tmp_path: Path) -> Path:
    path = tmp_path / "requirements.txt"
    path.write_text(
        "# a comment that must be ignored\n"
        "httpx==0.28.1\n"
        "pypdf==6.14.2\n"
        "fastapi==0.137.1\n"
        "uvicorn>=0.30          # not a == pin, so not checked\n",
        encoding="utf-8",
    )
    return path


def _run(requirements: Path, *flags: str) -> int:
    return check_pins.main(["check_pins.py", str(requirements), *flags])


# ── parsing ──────────────────────────────────────────────────────────────────


def test_only_exact_pins_are_read(requirements: Path) -> None:
    pins = check_pins.read_pins(requirements)
    assert pins == {"httpx": "0.28.1", "pypdf": "6.14.2", "fastapi": "0.137.1"}
    assert "uvicorn" not in pins, "a >= bound is not a pin and cannot drift"


def test_names_are_normalised(tmp_path: Path) -> None:
    """pip reports `pydantic-settings`; a file may say `pydantic_settings`."""
    path = tmp_path / "r.txt"
    path.write_text("pydantic_settings==2.14.1\n", encoding="utf-8")
    assert check_pins.read_pins(path) == {"pydantic-settings": "2.14.1"}


# ── the noise that made the job meaningless ──────────────────────────────────


def test_a_difference_the_baseline_also_shows_does_not_fail(
    requirements: Path, tmp_path: Path, capsys
) -> None:
    """fastapi resolves newer with *no* extra installed, so it is not the extra.

    This is the exact shape that failed all six jobs: pyproject allows newer
    than requirements.txt pins, and every extra inherits it.
    """
    base = _report(tmp_path, "base.json", {"httpx": "0.28.1", "fastapi": "0.141.1"})
    extra = _report(tmp_path, "extra.json", {"httpx": "0.28.1", "fastapi": "0.141.1"})

    assert _run(requirements, f"--report={extra}", f"--baseline={base}") == 0
    out = capsys.readouterr().out
    assert "fastapi" in out, "it must still be reported, just not fail"
    assert "without any extra" in out


# ── the signal that must survive ─────────────────────────────────────────────


def test_an_extra_that_downgrades_a_pinned_package_still_fails(
    requirements: Path, tmp_path: Path, capsys
) -> None:
    """The original defect: patent-client pinning httpx<0.28."""
    base = _report(tmp_path, "base.json", {"httpx": "0.28.1"})
    extra = _report(tmp_path, "extra.json", {"httpx": "0.27.2"})

    assert _run(requirements, f"--report={extra}", f"--baseline={base}") == 1
    out = capsys.readouterr().out
    assert "httpx" in out and "DOWNGRADED" in out


def test_an_extra_that_upgrades_a_pinned_package_also_fails(
    requirements: Path, tmp_path: Path, capsys
) -> None:
    """New coverage the baseline buys.

    Comparing against the pins could never see this: the pin/resolution gap
    looked identical whether the extra caused it or not.
    """
    base = _report(tmp_path, "base.json", {"pypdf": "6.14.2"})
    extra = _report(tmp_path, "extra.json", {"pypdf": "7.0.0"})

    assert _run(requirements, f"--report={extra}", f"--baseline={base}") == 1
    assert "upgraded" in capsys.readouterr().out


def test_an_allowed_difference_passes_at_exactly_the_reviewed_version(
    requirements: Path, tmp_path: Path
) -> None:
    base = _report(tmp_path, "base.json", {"httpx": "0.28.1"})
    extra = _report(tmp_path, "extra.json", {"httpx": "0.27.2"})
    flags = (f"--report={extra}", f"--baseline={base}")

    assert _run(requirements, *flags, "--allow=httpx:0.27.2") == 0
    # A different version than the one reviewed is not covered by the exception:
    # an accepted downgrade stays accepted only while it is the accepted one.
    assert _run(requirements, *flags, "--allow=httpx:0.26.0") == 1


def test_a_package_absent_from_the_resolution_is_not_drift(
    requirements: Path, tmp_path: Path
) -> None:
    base = _report(tmp_path, "base.json", {"httpx": "0.28.1"})
    extra = _report(tmp_path, "extra.json", {"httpx": "0.28.1"})
    assert _run(requirements, f"--report={extra}", f"--baseline={base}") == 0


def test_a_package_the_extra_introduces_below_the_pin_still_fails(
    requirements: Path, tmp_path: Path, capsys
) -> None:
    """No baseline entry means the extra brought it in — the pin is the ruler.

    This is the real `intel` case: pypdf is not a base dependency, so there is
    nothing to subtract, and 4.3.1 is still a version the codebase does not run
    on. Skipping it would let an extra ship a downgraded pinned package as long
    as the base resolution happened not to include it.
    """
    base = _report(tmp_path, "base.json", {"httpx": "0.28.1"})
    extra = _report(tmp_path, "extra.json", {"httpx": "0.28.1", "pypdf": "4.3.1"})

    assert _run(requirements, f"--report={extra}", f"--baseline={base}") == 1
    assert "DOWNGRADED" in capsys.readouterr().out


def test_a_package_the_extra_introduces_above_the_pin_does_not_fail(
    requirements: Path, tmp_path: Path, capsys
) -> None:
    """The real `file_ingest` case: markitdown pulls pypdf, newer than the pin.

    There is no base version for it to conflict with, so this is the same
    "PyPI moved on" noise as `ambient` — not an extra fighting the base.
    """
    base = _report(tmp_path, "base.json", {"httpx": "0.28.1"})
    extra = _report(tmp_path, "extra.json", {"httpx": "0.28.1", "pypdf": "6.15.0"})

    assert _run(requirements, f"--report={extra}", f"--baseline={base}") == 0
    assert "pypdf" in capsys.readouterr().out, "still reported, just not fatal"


# ── the installed-environment mode keeps its stricter rule ───────────────────


def test_without_a_report_any_difference_from_the_pins_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checking a real environment is a different question.

    There, requirements.txt *was* applied, so anything that does not match it is
    drift regardless of direction.
    """
    path = tmp_path / "r.txt"
    path.write_text("pytest==1.0.0\n", encoding="utf-8")
    assert check_pins.main(["check_pins.py", str(path)]) == 1


# ── argument handling ────────────────────────────────────────────────────────


def test_a_baseline_without_a_report_is_rejected(
    requirements: Path, tmp_path: Path
) -> None:
    base = _report(tmp_path, "base.json", {"httpx": "0.28.1"})
    assert _run(requirements, f"--baseline={base}") == 2


def test_a_missing_report_file_is_reported_not_ignored(requirements: Path) -> None:
    assert _run(requirements, "--report=/nonexistent/report.json") == 2


def test_a_requirements_file_with_no_pins_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("# nothing here\n", encoding="utf-8")
    assert check_pins.main(["check_pins.py", str(path)]) == 2


def test_the_script_is_importable_under_the_name_the_workflow_uses() -> None:
    """The workflow runs it by path from the repo root; keep that path true."""
    assert _SCRIPT.is_file()
    assert sys.version_info >= (3, 11)
