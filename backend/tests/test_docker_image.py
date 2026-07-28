"""Properties of the backend image that only fail in deployment.

Neither of these shows up in a test run or a local `uvicorn` — they surface as
a failed `docker compose up --build` or a container you cannot migrate, which
is the worst moment to discover them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text()


def _run_steps(dockerfile: str) -> list[str]:
    """RUN instructions, line continuations joined."""
    joined = re.sub(r"\\\s*\n\s*", " ", dockerfile)
    return [
        line.partition("RUN")[2].strip()
        for line in joined.splitlines()
        if line.startswith("RUN")
    ]


def test_no_unconditional_apt_layer(dockerfile: str) -> None:
    """apt is the least reliable step in the build and buys nothing here.

    Every package the image installs resolves to a prebuilt wheel on
    linux/x86_64/cp311; jieba is sdist-only but pure Python and builds with no
    compiler present. An unconditional `apt-get update` makes the build depend
    on the Debian archive being reachable and its release metadata current, and
    it fails with a bare `exit code: 100` when either is not true.
    """
    for step in _run_steps(dockerfile):
        if "apt-get" not in step:
            continue
        assert "INSTALL_BUILD_TOOLCHAIN" in step, (
            "apt must stay behind the opt-in build arg:\n" + step
        )


def test_build_toolchain_is_opt_in_and_defaults_off(dockerfile: str) -> None:
    assert re.search(r"^ARG INSTALL_BUILD_TOOLCHAIN=false\s*$", dockerfile, re.M), (
        "the toolchain arg must exist and default to false"
    )


def test_alembic_config_ships_in_the_image(dockerfile: str) -> None:
    """`docker compose exec backend alembic upgrade head` needs alembic.ini.

    Without it Alembic exits with "No config file 'alembic.ini' found" and a
    deployed database can only be migrated from a checkout.
    """
    copied: set[str] = set()
    for line in re.sub(r"\\\s*\n\s*", " ", dockerfile).splitlines():
        if line.startswith("COPY"):
            copied.update(line.split()[1:-1])
    assert "alembic.ini" in copied


def test_alembic_config_points_at_a_path_that_ships(dockerfile: str) -> None:
    """script_location must resolve inside the image, not just in a checkout."""
    ini = (DOCKERFILE.parent / "alembic.ini").read_text()
    location = re.search(r"^script_location\s*=\s*(.+)$", ini, re.M)
    assert location, "alembic.ini must declare script_location"
    # "%(here)s/app/db/alembic" — the app/ tree is COPYed, so this resolves.
    relative = location.group(1).strip().replace("%(here)s/", "")
    assert (DOCKERFILE.parent / relative).is_dir()
    assert relative.startswith("app/"), (
        f"script_location {relative!r} lives outside the COPYed app/ tree"
    )
