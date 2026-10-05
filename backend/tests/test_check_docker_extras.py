"""The Dockerfile-combination gate (scripts/check_docker_extras.py): its parsing, and that it fails when it should.

The resolution itself needs a package index and runs in CI (ci-deps.yml, job ``dockerfile-extras``); what is pinned
here is everything around it, because a gate that reads the wrong extras - or reports success without running - is
worse than none.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parents[2]
_SCRIPT = REPO / "scripts" / "check_docker_extras.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_docker_extras", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gate = _load()

DOCKERFILE = """\
FROM python:3.11-slim
RUN pip install --upgrade pip wheel \\
    && pip install -r requirements.txt
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \\
    -e ".[llm,science, intel ,file_ingest]" \\
    && pip install --extra-index-url https://download.pytorch.org/whl/cpu "pymupdf4llm==1.28.0" docling \\
    && pip uninstall -y surya-ocr 2>/dev/null; echo "removed"
RUN pip install "rapidocr==3.9.2" 'onnxruntime>=1.17' -c pins.txt && python -c "import rapidocr"
RUN echo "pip install not-a-requirement"
"""


def test_it_reads_the_extras_and_the_indexes_of_the_install():
    extras, indexes = gate.dockerfile_extras(DOCKERFILE)
    assert extras == ["llm", "science", "intel", "file_ingest"]
    assert indexes == ["https://download.pytorch.org/whl/cpu"]  # named twice, used once


def test_it_reads_every_other_requirement_the_dockerfile_gives_pip():
    """The later steps (docling, marker-pdf, the OCR runtime) install on top of the pins and can move them too."""
    assert gate.dockerfile_requirements(DOCKERFILE) == [
        "pip", "wheel", "pymupdf4llm==1.28.0", "docling", "rapidocr==3.9.2", "onnxruntime>=1.17",
    ]


def test_the_real_dockerfile_requirements_include_the_ocr_runtime_and_the_layout_parser():
    later = gate.dockerfile_requirements((REPO / "backend" / "Dockerfile").read_text(encoding="utf-8"))
    assert {"pymupdf4llm==1.28.0", "rapidocr==3.9.2", "docling"} <= set(later), later
    assert "requirements.txt" not in later
    assert not any(item.startswith(".[") or item.startswith("-") for item in later), later


def test_the_real_dockerfile_is_parsed_into_the_extras_it_installs():
    extras, indexes = gate.dockerfile_extras((REPO / "backend" / "Dockerfile").read_text(encoding="utf-8"))
    assert {"intel", "file_ingest", "llm", "science"} <= set(extras)
    assert "patents" not in extras, "patent-client replaces the pinned httpx and pypdf; it must not ride into the image"
    assert indexes and all(url.startswith("https://") for url in indexes)


def test_a_dockerfile_without_the_install_is_an_error_not_an_empty_pass():
    with pytest.raises(SystemExit):
        gate.dockerfile_extras("FROM python:3.11-slim\nRUN pip install fastapi\n")


def test_requirements_become_a_constraints_file_both_pip_and_uv_accept():
    text = (
        "# comment\n"
        "-r other.txt\n"
        "\n"
        "uvicorn[standard]==0.49.0\n"
        "tenacity>=8.2.0          # inline comment\n"
        "uvloop==0.22.1; sys_platform != 'win32'\n"
    )
    assert gate.constraints_from_requirements(text).splitlines() == [
        "uvicorn==0.49.0",
        "tenacity>=8.2.0",
        "uvloop==0.22.1; sys_platform != 'win32'",
    ]


def test_the_real_requirements_survive_the_conversion():
    out = gate.constraints_from_requirements((REPO / "backend" / "requirements.txt").read_text(encoding="utf-8"))
    names = {Requirement(line).name.lower() for line in out.splitlines()}
    assert {"httpx", "pypdf", "uvicorn", "fastapi"} <= names
    assert "[" not in out


def test_the_resolver_is_asked_for_every_extra_with_the_pins_as_constraints(tmp_path):
    command = gate.resolve_command(
        ["llm", "intel"], ["https://download.pytorch.org/whl/cpu"], tmp_path / "pins.txt", tmp_path / "out.txt", "3.11",
        tmp_path / "later.in",
    )
    assert command[:3] == ["uv", "pip", "compile"]
    assert str(tmp_path / "later.in") in command  # the later steps are resolved together with the extras
    assert command[command.index("-c") + 1] == str(tmp_path / "pins.txt")
    assert [command[i + 1] for i, a in enumerate(command) if a == "--extra"] == ["llm", "intel"]
    assert "--index-strategy" in command  # pip looks at every index; uv would stop at the first one


def _run_main(monkeypatch, tmp_path, *, returncode: int, uv: bool = True) -> int:
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/uv" if uv else None)

    def fake_run(command, **kwargs):
        # a real resolver writes the compiled file the script then counts
        Path(command[command.index("-o") + 1]).write_text("fastapi==1\n    # via x\nhttpx==2\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode, "", "x conflicts with y")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    return gate.main([])


def test_a_conflict_fails_the_gate(monkeypatch, tmp_path, capsys):
    assert _run_main(monkeypatch, tmp_path, returncode=1) == 1
    err = capsys.readouterr().err
    assert "conflicts" in err and "replacing a pinned package" in err


def test_a_clean_resolution_passes(monkeypatch, tmp_path, capsys):
    assert _run_main(monkeypatch, tmp_path, returncode=0) == 0
    assert "resolved 2 packages" in capsys.readouterr().out


def test_a_missing_resolver_is_not_a_pass(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, returncode=0, uv=False) == 2
