"""Hook wiring: optimize success notifies dossier optimize_completed."""
from __future__ import annotations

from app.worker.tasks import _notify_dossier_optimize_completed


def test_notify_optimize_prefers_campaign(monkeypatch):
    calls: list[tuple] = []

    def fake_campaign(cid, event, **kwargs):
        calls.append(("campaign", cid, event))
        return {"ok": True}

    def fake_project(pid, event, **kwargs):
        calls.append(("project", pid, event))
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.wiki.dossier.notify_dossier_event_for_campaign",
        fake_campaign,
    )
    monkeypatch.setattr(
        "app.services.wiki.dossier.notify_dossier_event",
        fake_project,
    )
    _notify_dossier_optimize_completed(
        {
            "workbench_campaign_id": 42,
            "requirement": {"project_id": "should-not-use", "domain": "anticorrosion_coating"},
        }
    )
    assert calls == [("campaign", 42, "optimize_completed")]


def test_notify_optimize_falls_back_to_project_id(monkeypatch):
    calls: list[tuple] = []

    monkeypatch.setattr(
        "app.services.wiki.dossier.notify_dossier_event_for_campaign",
        lambda *a, **k: calls.append(("campaign", a, k)),
    )
    monkeypatch.setattr(
        "app.services.wiki.dossier.notify_dossier_event",
        lambda pid, event, **kwargs: calls.append(("project", pid, event)),
    )
    _notify_dossier_optimize_completed(
        {"requirement": {"project_id": "proj-opt-1", "domain": "anticorrosion_coating"}}
    )
    assert calls == [("project", "proj-opt-1", "optimize_completed")]


def test_notify_optimize_swallows_errors(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.services.wiki.dossier.notify_dossier_event",
        boom,
    )
    # Must not raise
    _notify_dossier_optimize_completed({"requirement": {"project_id": "x"}})
