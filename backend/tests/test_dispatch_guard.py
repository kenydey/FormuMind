"""Async submission when the task broker is unreachable.

Reproduces the outage this guard exists for: with Redis down, ``.delay()``
spends ~19 seconds retrying the Celery *result backend* and then raises
``RuntimeError: Retry limit exceeded ...``, which reaches the browser as a
plain-text 500. The frontend parses error bodies as JSON, so an unparseable
body collapses to ``"<path> -> <status>"`` — no cause, no next step. Behind
nginx the same outage reads as a 502 instead.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import _dispatch
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def broker_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: False)


REQUIREMENT = {"domain": "anticorrosion_coating", "substrate": "carbon_steel"}

# Every endpoint that hands work to Celery, with a minimal valid body.
SUBMISSIONS = [
    ("/api/research/recommend", REQUIREMENT),
    ("/api/research/deep", {"topic": "环氧防腐", "requirement": REQUIREMENT}),
    ("/api/optimize", {"requirement": REQUIREMENT, "iterations": 2}),
    ("/api/design/inverse", {"requirement": REQUIREMENT}),
    ("/api/search/stream", {"query": "环氧防腐涂料"}),
    ("/api/loop/iterate", {"domain": "anticorrosion_coating", "substrate": "carbon_steel"}),
]


@pytest.mark.parametrize("path,body", SUBMISSIONS, ids=[p for p, _ in SUBMISSIONS])
def test_unreachable_broker_returns_503_not_500(
    client: TestClient, broker_down: None, path: str, body: dict
) -> None:
    """503 says "temporarily unavailable, the work is recorded". 500 says
    "this endpoint is broken", which sends the user to the wrong place."""
    response = client.post(path, json=body)
    assert response.status_code == 503, path


@pytest.mark.parametrize("path,body", SUBMISSIONS, ids=[p for p, _ in SUBMISSIONS])
def test_error_body_is_json_the_frontend_can_read(
    client: TestClient, broker_down: None, path: str, body: dict
) -> None:
    """The UI falls back to a bare "<path> -> <status>" for any body it cannot
    parse as JSON with a string ``detail`` — which is precisely the useless
    message this fix removes."""
    detail = client.post(path, json=body).json()["detail"]
    assert isinstance(detail, str)
    # Actionable: names what is down and what to do about it.
    assert "Redis" in detail
    assert "worker" in detail


def test_submission_does_not_wait_for_celery_to_exhaust_its_retries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe replaces ~19s of Celery retries, so the caller is not left
    holding a request long enough for a proxy to time out on its own."""
    calls: list[str] = []

    def explode(_payload):
        calls.append("delay")
        raise RuntimeError("Retry limit exceeded while trying to reconnect")

    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: False)
    from app.worker import tasks

    monkeypatch.setattr(tasks.run_recommend_task, "delay", explode)

    assert client.post("/api/research/recommend", json=REQUIREMENT).status_code == 503
    assert calls == [], "delay() must not be reached once the probe says the broker is down"


def test_dispatch_failure_after_a_passing_probe_is_still_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broker that dies between the probe and the publish, or a payload
    Celery cannot serialise, must not fall through to a raw 500."""
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)
    from app.worker import tasks

    def explode(_payload):
        raise RuntimeError("Retry limit exceeded while trying to reconnect")

    monkeypatch.setattr(tasks.run_recommend_task, "delay", explode)

    response = client.post("/api/research/recommend", json=REQUIREMENT)
    assert response.status_code == 503
    assert isinstance(response.json()["detail"], str)


# ── the probe itself ─────────────────────────────────────────────────────────


def test_eager_mode_needs_no_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eager runs tasks in-process; refusing to submit would break the default
    single-process deployment, which has no Redis at all."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", True, raising=False)
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0", raising=False)
    assert _dispatch.broker_reachable() is True


def test_closed_port_is_reported_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]  # bound, never listening

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(
        settings, "redis_url", f"redis://127.0.0.1:{closed_port}/0", raising=False
    )
    assert _dispatch.broker_reachable() is False


def test_unparseable_broker_url_is_not_treated_as_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A unix socket has no host/port to probe. Blocking submission on a check
    that could not run would turn a working deployment into a broken one."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(settings, "redis_url", "redis+socket:///var/run/redis.sock", raising=False)
    assert _dispatch.broker_reachable() is True


def test_recorded_work_survives_the_outage(
    broker_down: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outbox row is written before dispatch, so a refused submission is
    delayed work rather than lost work — dispatcher.recover_stalled re-enqueues
    it once the broker returns.

    Runs against its own database. Against the shared one this asserts on rows
    other tests wrote, and the idempotency key can match an earlier submission
    whose row has since moved past PENDING — a pass or fail decided by test
    ordering rather than by the behaviour under test.
    """
    from sqlalchemy import select

    from app.db import database as db_mod
    from app.db.models import TaskOutbox
    from tests.alembic_helpers import run_upgrade

    db_url = f"sqlite:///{tmp_path}/dispatch_outbox.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    db_mod._default.clear()

    with TestClient(app) as client:
        assert client.post("/api/research/recommend", json=REQUIREMENT).status_code == 503

    with db_mod.default_session_factory()() as session:
        rows = session.execute(
            select(TaskOutbox).filter_by(operation="research_recommend")
        ).scalars().all()

    assert len(rows) == 1, "the refused submission must leave exactly one durable row"
    assert rows[0].status == "PENDING", "the row must stay claimable by recover_stalled"
    assert rows[0].payload, "an empty payload could not be re-dispatched"


def test_every_outbox_operation_can_be_recovered() -> None:
    """A row the dispatcher cannot map is a job that silently never runs.
    ``inverse_design`` was exactly that until it was added to the mapping."""
    from app.db import dispatcher

    dispatched: list[str] = []

    class _Recorder:
        def __init__(self, name: str) -> None:
            self.name = name

        def delay(self, _payload):
            dispatched.append(self.name)

    import app.worker.tasks as tasks

    originals = {}
    for attr in ("run_recommend_task", "run_deep_research_task", "run_inverse_design_task"):
        originals[attr] = getattr(tasks, attr)
        setattr(tasks, attr, _Recorder(attr))
    try:
        for operation in ("research_recommend", "research_deep", "inverse_design"):
            dispatcher._dispatch(operation, {})
    finally:
        for attr, original in originals.items():
            setattr(tasks, attr, original)

    assert dispatched == [
        "run_recommend_task",
        "run_deep_research_task",
        "run_inverse_design_task",
    ]


# ── health ───────────────────────────────────────────────────────────────────


def test_health_reports_the_task_broker(client: TestClient) -> None:
    """A broker outage breaks every async feature while the process keeps
    answering requests, so /health must not report ``ok`` through it."""
    body = client.get("/health").json()
    assert "task_broker" in body
    assert set(body["task_broker"]) == {"required", "reachable"}


def test_health_is_degraded_when_the_broker_is_unreachable(
    client: TestClient, broker_down: None
) -> None:
    body = client.get("/health").json()
    assert body["task_broker"]["reachable"] is False
    assert body["status"] == "degraded"


def test_degraded_health_still_answers_200(
    client: TestClient, broker_down: None
) -> None:
    """The container healthcheck asserts HTTP 200, and restarting the API
    would not bring Redis back — degraded must stay reportable, not fatal."""
    assert client.get("/health").status_code == 200


# ── a UI toggle must not overrule the deployment ─────────────────────────────


def test_ui_saved_eager_flag_cannot_override_the_deployment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """docker-compose sets FORMUMIND_CELERY_EAGER=false and runs a worker.

    The flag is toggleable in the Settings UI, and persisted UI values are
    promoted into os.environ so they outrank compose. For product flags that
    is the point; for this one it silently disables the worker container and
    moves every async job back inside the HTTP request, where a long research
    run can outlast an nginx read timeout.
    """
    from app.services import secrets_store

    env_file = tmp_path / "runtime.env"
    env_file.write_text("FORMUMIND_CELERY_EAGER=true\n")
    monkeypatch.setattr(secrets_store, "read_env_file", lambda: {"FORMUMIND_CELERY_EAGER": "true"})
    monkeypatch.setenv("FORMUMIND_CELERY_EAGER", "false")

    secrets_store.apply_persisted_ui_settings()

    import os

    assert os.environ["FORMUMIND_CELERY_EAGER"] == "false"


def test_ui_saved_eager_flag_still_applies_when_nobody_set_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a deployment value there is nothing to overrule, so the UI
    toggle keeps working — a single-process install can still choose eager."""
    from app.services import secrets_store

    monkeypatch.setattr(secrets_store, "read_env_file", lambda: {"FORMUMIND_CELERY_EAGER": "true"})
    monkeypatch.delenv("FORMUMIND_CELERY_EAGER", raising=False)

    secrets_store.apply_persisted_ui_settings()

    import os

    assert os.environ["FORMUMIND_CELERY_EAGER"] == "true"


def test_ordinary_feature_flags_still_win_over_a_stale_container_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The promotion exists because compose injects env at container creation,
    so a stale key would beat what the user last saved. Only topology flags are
    exempt — narrowing it further would restore the original bug."""
    from app.services import secrets_store

    monkeypatch.setattr(
        secrets_store, "read_env_file", lambda: {"FORMUMIND_ARXIV_SEARCH_ENABLED": "true"}
    )
    monkeypatch.setenv("FORMUMIND_ARXIV_SEARCH_ENABLED", "false")

    secrets_store.apply_persisted_ui_settings()

    import os

    assert os.environ["FORMUMIND_ARXIV_SEARCH_ENABLED"] == "true"
