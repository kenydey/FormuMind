"""The topic radar finally has a way to run (round-3 P2-8).

``formumind.topic_sweep`` was reachable only through a commented-out beat schedule; its docstring
claimed an API could start it, and none could. Now: ``POST /api/search/topic-sweep`` for one
sweep, and a schedule built from ``FORMUMIND_TOPIC_RADAR_*`` for the periodic ones.
"""
from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.domain.schemas import Evidence
from app.main import app
from app.worker.topic_radar import DEFAULT_CRON, build_beat_schedule, parse_topics


def _settings(**kw) -> Settings:
    return Settings(**kw)


def _topics(*items: dict) -> str:
    return json.dumps(list(items), ensure_ascii=False)


# ── parsing ──────────────────────────────────────────────────────────────────


def test_a_topic_gets_its_defaults():
    [topic] = parse_topics(_topics({"query": " 镁合金 无铬钝化 "}))
    assert topic == {
        "query": "镁合金 无铬钝化",
        "project_id": None,
        "cron": DEFAULT_CRON,
        "total_limit": 100,
        "per_source_cap": 30,
        "source_types": [],
    }


def test_limits_are_clamped_to_what_the_search_accepts():
    [topic] = parse_topics(_topics({"query": "q", "total_limit": 99999, "per_source_cap": 0}))
    assert topic["total_limit"] == 1000 and topic["per_source_cap"] == 1


@pytest.mark.parametrize("raw", ["", "   ", "not json", '{"query": "q"}', "42", "null"])
def test_nothing_usable_means_no_topics(raw):
    assert parse_topics(raw) == []


def test_bad_entries_are_skipped_and_the_good_ones_survive(caplog):
    raw = _topics(
        {"query": "good one", "project_id": "p1", "cron": "30 2 * * 3"},
        "not an object",
        {"project_id": "no query"},
        {"query": "bad cron", "cron": "every monday"},
        {"query": "short cron", "cron": "0 1 *"},
        {"query": "bad limit", "total_limit": "lots"},
        {"query": "bad types", "source_types": "literature"},
        {"query": "also good", "source_types": ["patents"]},
    )
    with caplog.at_level(logging.WARNING, logger="app.worker.topic_radar"):
        topics = parse_topics(raw)
    assert [t["query"] for t in topics] == ["good one", "also good"]
    assert topics[0]["project_id"] == "p1" and topics[0]["cron"] == "30 2 * * 3"
    assert topics[1]["source_types"] == ["patents"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 6  # one per bad entry


# ── the schedule ─────────────────────────────────────────────────────────────


def test_the_radar_is_off_by_default():
    assert _settings().topic_radar_enabled is False
    assert build_beat_schedule(_settings(topic_radar_topics=_topics({"query": "q"}))) == {}


def test_enabled_topics_become_beat_entries():
    settings = _settings(
        topic_radar_enabled=True,
        topic_radar_topics=_topics(
            {"query": "镁合金 无铬钝化", "project_id": "p1"},
            {"query": "Epoxy zinc primer!", "cron": "15 3 1 * *", "total_limit": 40, "source_types": ["patents"]},
        ),
    )
    schedule = build_beat_schedule(settings)

    assert list(schedule) == ["topic-radar-0-topic", "topic-radar-1-epoxy-zinc-primer"]
    first, second = schedule.values()
    assert first["task"] == second["task"] == "formumind.topic_sweep"
    assert first["args"] == (
        {"query": "镁合金 无铬钝化", "project_id": "p1", "total_limit": 100, "per_source_cap": 30, "source_types": []},
    )
    assert second["args"][0]["total_limit"] == 40 and second["args"][0]["source_types"] == ["patents"]

    weekly, monthly = first["schedule"], second["schedule"]
    assert (weekly.minute, weekly.hour, weekly.day_of_week) == ({0}, {1}, {1})  # Mondays 01:00
    assert (monthly.minute, monthly.hour, monthly.day_of_month) == ({15}, {3}, {1})  # the 1st, 03:15


def test_enabled_without_a_usable_topic_schedules_nothing_and_says_so(caplog):
    with caplog.at_level(logging.WARNING, logger="app.worker.topic_radar"):
        assert build_beat_schedule(_settings(topic_radar_enabled=True, topic_radar_topics="oops")) == {}
    assert any("defines no valid sweep" in r.getMessage() for r in caplog.records)


def test_the_celery_app_carries_the_built_schedule():
    from app.worker.celery_app import celery_app

    assert celery_app.conf.beat_schedule == build_beat_schedule(get_settings())


# ── the on-demand endpoint ───────────────────────────────────────────────────


def _ev(identifier: str) -> Evidence:
    return Evidence(source="OpenAlex", identifier=identifier, title=identifier, snippet="abstract", relevance=0.9)


def _wait_terminal(client: TestClient, status_url: str, timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    body: dict = {}
    while time.time() < deadline:
        body = client.get(status_url).json()
        if body.get("state") in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"task did not finish: {body}")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_a_manual_sweep_runs_the_radar_task_and_reports_what_it_found(client, monkeypatch):
    seen: dict = {}

    def fake_iter(query, source_types, req=None, total_limit=0, per_source_cap=0, **_kw):
        seen.update(query=query, source_types=list(source_types), total_limit=total_limit, per_source_cap=per_source_cap)
        return ([_ev("10.1000/a"), _ev("10.1000/b")], {})

    def fake_dispatch(evidence, project_id=None, query=None, **_kw):
        seen.update(project_id=project_id, ingested=len(evidence))
        return "kb-task-7"

    monkeypatch.setattr("app.services.literature.iter_search", fake_iter)
    monkeypatch.setattr("app.worker.tasks.dispatch_kb_ingest", fake_dispatch)

    res = client.post("/api/search/topic-sweep", json={"query": "  镁合金 无铬钝化 ", "project_id": "p1", "total_limit": 40})
    assert res.status_code == 202, res.text
    status = _wait_terminal(client, res.json()["status_url"])

    assert status["kind"] == "topic_sweep" and status["state"] == "completed"
    assert status["result"]["found"] == 2 and status["result"]["ingest_task_id"] == "kb-task-7"
    assert seen["query"] == "镁合金 无铬钝化"  # trimmed
    assert seen["source_types"] == list(get_settings().federated_sources)  # none given → the defaults
    assert (seen["total_limit"], seen["per_source_cap"]) == (40, 30)
    assert (seen["project_id"], seen["ingested"]) == ("p1", 2)


def test_a_sweep_that_finds_nothing_starts_no_ingest(client, monkeypatch):
    monkeypatch.setattr("app.services.literature.iter_search", lambda *a, **k: ([], {}))
    monkeypatch.setattr(
        "app.worker.tasks.dispatch_kb_ingest",
        lambda *a, **k: pytest.fail("nothing found, nothing to ingest"),
    )
    res = client.post("/api/search/topic-sweep", json={"query": "nonexistent chemistry"})
    status = _wait_terminal(client, res.json()["status_url"])
    assert status["state"] == "completed"
    assert status["result"]["found"] == 0 and status["result"]["ingest_task_id"] is None


def test_a_failing_sweep_is_reported_as_failed(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("search backend exploded")

    monkeypatch.setattr("app.services.literature.iter_search", boom)
    res = client.post("/api/search/topic-sweep", json={"query": "anything"})
    status = _wait_terminal(client, res.json()["status_url"])
    assert status["state"] == "failed"
    assert "search backend exploded" in (status["result"] or {}).get("error", "")


@pytest.mark.parametrize("body", [{"query": ""}, {"query": "q", "total_limit": 0}, {"query": "q", "per_source_cap": 500}, {}])
def test_the_endpoint_validates_its_input(client, body):
    assert client.post("/api/search/topic-sweep", json=body).status_code == 422
