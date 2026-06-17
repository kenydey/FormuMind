"""LAMMPS / HTPolyNet interface skeleton (v0.5, P7).

This module provides the booking layer for submitting molecular dynamics
cure simulations to a local LAMMPS installation (via Docker or system
executable). When LAMMPS is unavailable the module is inert — all public
functions return None / placeholder values so callers never branch.

Activation requires:
  1. Docker: ``docker compose up lammps`` (see docker-compose.yml)
  2. Set ``LAMMPS_EXEC`` environment variable to the binary path inside
     the container (e.g. ``/usr/bin/lmp``).

Usage:
  from .md_simulation import submit_cure_simulation, fetch_simulation_result
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _lammps_available() -> bool:
    """True when LAMMPS_EXEC is set and the binary exists (or is reachable)."""
    exe = os.environ.get("LAMMPS_EXEC", "")
    if not exe:
        return False
    p = Path(exe)
    # If it looks like a real path, verify it
    if p.is_absolute():
        return p.exists()
    return True  # trust the env var for container paths


def _generate_lammps_input(form_name: str, cure_temp_k: float, output_dir: Path) -> Path:
    """Write a minimal LAMMPS input template for a cure simulation.

    The template uses HTPolyNet-compatible variable names and is designed
    to be filled in by HTPolyNet's packing + crosslinking routines.
    Returns the path to the generated input file.
    """
    input_content = f"""# FormuMind auto-generated LAMMPS input
# Formulation: {form_name}
# Cure temperature: {cure_temp_k:.1f} K

units           real
atom_style      full
boundary        p p p

# --- HTPolyNet integration point ---
# Run: htpolynet run -dirc project.yaml
# This template is replaced by HTPolyNet during the cure protocol.

variable        TEMP equal {cure_temp_k:.1f}
variable        TDAMP equal 100.0   # fs
variable        PDAMP equal 1000.0  # fs
variable        RUNTIME equal 100000  # steps (100 ps at 1 fs/step)

# NVT equilibration at cure temperature
fix             NVT all nvt temp ${{TEMP}} ${{TEMP}} ${{TDAMP}}
thermo          1000
run             ${{RUNTIME}}
unfix           NVT

# Results written to: {output_dir}/cure_report.log
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    inp_path = output_dir / "in.cure"
    inp_path.write_text(input_content)
    return inp_path


def submit_cure_simulation(
    form_name: str,
    cure_temp_c: float = 80.0,
    job_dir: str | None = None,
) -> dict[str, Any] | None:
    """Prepare and (optionally) submit a LAMMPS cure simulation job.

    When LAMMPS is unavailable, returns None without raising.
    When available, writes the input file and returns job metadata
    (path, status, instructions) — actual submission is left to the
    operator to trigger via ``docker compose run lammps lmp -in ...``.

    This function intentionally does NOT block or wait for completion;
    call ``fetch_simulation_result()`` to retrieve results later.
    """
    if not _lammps_available():
        return None

    import uuid

    job_id = uuid.uuid4().hex[:8]
    base = Path(job_dir or "./data/md_jobs")
    output_dir = base / job_id
    inp = _generate_lammps_input(form_name, cure_temp_c + 273.15, output_dir)
    return {
        "job_id": job_id,
        "status": "prepared",
        "input_file": str(inp),
        "cure_temperature_k": round(cure_temp_c + 273.15, 1),
        "instructions": (
            f"Run: docker compose run --rm lammps lmp -in {inp} "
            f"| tee {output_dir}/cure_report.log"
        ),
        "engine": "lammps-htpolynet",
    }


def fetch_simulation_result(job_id: str, job_dir: str | None = None) -> dict[str, Any] | None:
    """Poll for LAMMPS job results.

    Looks for ``cure_report.log`` in the job directory. Returns None if
    LAMMPS is unavailable or the job has not completed.

    When the log file exists, parses the last thermo output line for
    temperature and potential energy as a minimal result dict.
    """
    if not _lammps_available():
        return None

    base = Path(job_dir or "./data/md_jobs")
    log_path = base / job_id / "cure_report.log"
    if not log_path.exists():
        return {"job_id": job_id, "status": "pending", "engine": "lammps-htpolynet"}

    # Minimal log parse: look for the last "Step ... Temp ... PotEng ..." line
    lines = log_path.read_text().splitlines()
    last_thermo = None
    for line in reversed(lines):
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            last_thermo = parts
            break

    return {
        "job_id": job_id,
        "status": "completed" if last_thermo else "running",
        "last_step": int(last_thermo[0]) if last_thermo else None,
        "temperature_k": float(last_thermo[1]) if last_thermo and len(last_thermo) > 1 else None,
        "engine": "lammps-htpolynet",
    }
