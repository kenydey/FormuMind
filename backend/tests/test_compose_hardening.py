"""The compose files must not publish credentials or unauthenticated data stores (round-3 P3-13).

``docker-compose.yml`` wrote the Neo4j password in plain text in five places and published Neo4j
(7474 / 7687) and the password-less Redis broker (6379) on every network interface. Passwords now
come from ``.env``; the two data stores are reachable from the host's loopback only.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILES = sorted(REPO.glob("docker-compose*.yml"))
BASE = REPO / "docker-compose.yml"
SHIPPED_DEFAULT = "formumind123"
ALLOWED_DEFAULT_EXPR = "${FORMUMIND_NEO4J_PASSWORD:-formumind123}"

pytestmark = pytest.mark.skipif(not BASE.is_file(), reason="compose files not present (backend-only checkout)")


def _base() -> dict:
    return yaml.safe_load(BASE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_the_neo4j_password_is_never_a_bare_literal(path):
    text = path.read_text(encoding="utf-8")
    # The only sanctioned appearance is the fallback inside the variable reference, which keeps
    # an install that was initialised with the old default working.
    assert SHIPPED_DEFAULT not in text.replace(ALLOWED_DEFAULT_EXPR, ""), (
        f"{path.name}: write the Neo4j password as ${{FORMUMIND_NEO4J_PASSWORD:-…}}, not a literal"
    )


def test_every_service_gets_the_password_from_the_variable():
    for name, service in _base()["services"].items():
        env = service.get("environment") or {}
        entries = [f"{k}={v}" for k, v in env.items()] if isinstance(env, dict) else list(env)
        for entry in entries:
            if "NEO4J_PASSWORD" in entry or entry.startswith("NEO4J_AUTH"):
                assert "${FORMUMIND_NEO4J_PASSWORD" in entry, f"{name}: {entry}"


def test_the_graph_healthcheck_uses_the_configured_password():
    test = " ".join(_base()["services"]["kg"]["healthcheck"]["test"])
    assert "$$FORMUMIND_NEO4J_PASSWORD" in test  # the container's own variable, not a literal


@pytest.mark.parametrize("service", ["kg", "redis"])
def test_data_stores_are_published_on_loopback_only(service):
    ports = _base()["services"][service].get("ports") or []
    assert ports, f"{service} is expected to publish ports for host-side tools"
    assert all(str(p).startswith("127.0.0.1:") for p in ports), (
        f"{service} publishes {ports}: bind to 127.0.0.1, or publish deliberately behind a firewall"
    )


def test_the_radar_beat_process_is_opt_in():
    beat = _base()["services"]["beat"]
    assert beat["profiles"] == ["radar"]  # a default `up` must not start a second scheduler
    assert "celery" in beat["command"] and " beat " in f" {beat['command']} "
    assert beat["healthcheck"] == {"disable": True}  # it would inherit the uvicorn probe it cannot pass
