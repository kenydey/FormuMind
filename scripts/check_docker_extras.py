#!/usr/bin/env python3
"""Resolve the extras the Dockerfile installs against requirements.txt, without installing anything.

The production image is ``pip install -r requirements.txt`` followed by ``pip install -e ".[<many extras>]"``. The
second step is free to *replace* what the first pinned, and it did: ``patent-client`` (``httpx<0.28``, ``pypdf<5``)
put httpx 0.27.2 and pypdf 4.3.1 in the image - 49 published advisories on the pypdf alone, which reads every uploaded
and downloaded PDF. Nothing could see it: ``ci-deps.yml`` resolves one extra at a time, and the image is not built in
CI. Raising a floor made it worse - ``file_ingest`` then wanted ``pypdf>=6.19`` while ``patent-client`` wanted
``pypdf<5``, so the Dockerfile's combination stopped resolving at all and only ``docker compose build`` would have said so.

This resolves the Dockerfile's exact list - the extras *and* every package it names in later ``pip install`` steps
(docling, marker-pdf, rapidocr, ...) - with the pins as *constraints*, so a conflict is an error instead of a silent
downgrade. It uses ``uv pip compile`` (resolution only: nothing is downloaded beyond metadata, nothing built).

    python scripts/check_docker_extras.py [--python-version 3.11]

Exit status: 0 when the combination resolves with every pin held, 1 when it does not, 2 when ``uv`` is missing.
"""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"

_EDITABLE = re.compile(r'-e\s+"\.\[([^\]]+)\]"')
_EXTRA_INDEX = re.compile(r"--extra-index-url\s+(\S+)")
_EXTRAS_IN_REQUIREMENT = re.compile(r"\[[^\]]*\]")
_COMMENT = re.compile(r"(^|\s)#.*$")


def dockerfile_extras(text: str) -> tuple[list[str], list[str]]:
    """The extras of the Dockerfile's editable install, and every extra index URL the Dockerfile gives pip."""
    match = _EDITABLE.search(text)
    if not match:
        raise SystemExit('no `-e ".[...]"` install found in the Dockerfile')
    extras = [name.strip() for name in match.group(1).split(",") if name.strip()]
    return extras, list(dict.fromkeys(_EXTRA_INDEX.findall(text)))


# pip options that take a value; the value is not a requirement.
_VALUE_OPTIONS = {"-r", "-c", "-e", "-i", "--index-url", "--extra-index-url", "-f", "--find-links", "--constraint", "--requirement", "--editable"}


def dockerfile_requirements(text: str) -> list[str]:
    """Every requirement the Dockerfile hands to ``pip install`` besides requirements.txt and the editable install.

    The image is built in steps, and the later ones (docling, marker-pdf, a pinned PyMuPDF, the OCR runtime) install
    on top of what the first ones pinned - so they can move a pin too. Quoting, line continuations and ``&&`` / ``;``
    chains are handled; ``pip uninstall`` and non-pip commands are ignored.
    """
    requirements: list[str] = []
    for instruction in re.sub(r"\\\s*\n", " ", text).splitlines():
        instruction = instruction.strip()
        if not instruction.upper().startswith("RUN "):
            continue
        for segment in re.split(r"&&|;", instruction[4:]):
            try:
                tokens = shlex.split(segment)
            except ValueError:
                continue
            if tokens[:2] != ["pip", "install"]:
                continue
            skip = False
            for token in tokens[2:]:
                if skip:
                    skip = False
                elif token in _VALUE_OPTIONS:
                    skip = True
                elif not token.startswith("-"):
                    requirements.append(token)
    return list(dict.fromkeys(requirements))


def constraints_from_requirements(text: str) -> str:
    """``requirements.txt`` as a constraints file: comments and options dropped, extras removed (pip and uv reject
    them in a constraints file)."""
    lines = []
    for raw in text.splitlines():
        line = _COMMENT.sub("", raw).strip()
        if not line or line.startswith("-"):
            continue
        lines.append(_EXTRAS_IN_REQUIREMENT.sub("", line))
    return "\n".join(lines) + "\n"


def resolve_command(
    extras: list[str],
    indexes: list[str],
    constraints: Path,
    output: Path,
    python_version: str,
    extra_requirements: Path | None = None,
) -> list[str]:
    command = [
        "uv", "pip", "compile", str(BACKEND / "pyproject.toml"),
        *([str(extra_requirements)] if extra_requirements else []),
        "--python-version", python_version,
        "-c", str(constraints),
        "-o", str(output),
    ]
    for extra in extras:
        command += ["--extra", extra]
    for index in indexes:
        command += ["--extra-index-url", index]
    if indexes:
        # pip looks at every index for every package; uv's default stops at the first index that has the name.
        command += ["--index-strategy", "unsafe-best-match"]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve the Dockerfile's extras against requirements.txt.")
    parser.add_argument("--python-version", default="3.11", help="the image's Python (default: 3.11)")
    parser.add_argument("--dockerfile", type=Path, default=BACKEND / "Dockerfile")
    parser.add_argument("--requirements", type=Path, default=BACKEND / "requirements.txt")
    args = parser.parse_args(argv)

    if shutil.which("uv") is None:
        print("uv is not installed (pip install uv)", file=sys.stderr)
        return 2

    dockerfile = args.dockerfile.read_text(encoding="utf-8")
    extras, indexes = dockerfile_extras(dockerfile)
    later = dockerfile_requirements(dockerfile)
    constraints = constraints_from_requirements(args.requirements.read_text(encoding="utf-8"))
    print(f"Dockerfile installs {len(extras)} extras: {', '.join(extras)}")
    print(f"and {len(later)} more requirements in later steps: {', '.join(later)}")

    with tempfile.TemporaryDirectory() as tmp:
        constraints_path = Path(tmp) / "pins.txt"
        constraints_path.write_text(constraints, encoding="utf-8")
        output = Path(tmp) / "resolved.txt"
        later_path = Path(tmp) / "dockerfile-requirements.in"
        later_path.write_text("\n".join(later) + "\n", encoding="utf-8")
        proc = subprocess.run(
            resolve_command(extras, indexes, constraints_path, output, args.python_version, later_path),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(proc.stderr or proc.stdout, file=sys.stderr)
            print(
                "\nThe extras the Dockerfile installs do not resolve against backend/requirements.txt. Left alone, pip "
                "would resolve this by replacing a pinned package with an older one. Drop the extra that conflicts "
                "(or move the offending package to a separate extra the Dockerfile does not install).",
                file=sys.stderr,
            )
            return 1
        resolved = sum(1 for line in output.read_text(encoding="utf-8").splitlines() if re.match(r"^[A-Za-z0-9]", line))

    print(f"resolved {resolved} packages with every pin in {args.requirements.name} held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
