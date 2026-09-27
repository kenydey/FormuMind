"""Tests for W5-4 (P1-28): review-runs read API + manual re-run.

Covers: list (empty / filter / ordering), detail 200 + 404, warn/fail counts
from dispositions, stale fields defaulting (W5-3 contract), rerun 404/400 and
the 200 happy path with the fix-loop entrypoint mocked (no LLM).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import review_runs as api_mod
from app.services import reviewer_fix_loop as svc


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def api_client(data_dir):
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


def _seed_run(session_key="key-1", project_id="proj-1", outcome="flagged", **extra):
    run = svc.new_review_run(session_key=session_key, project_id=project_id)
    run = svc.finish_review_run(run, outcome=outcome)
    run.update(extra)
    # run_id 含毫秒时间戳：同一毫秒内 session_key 相同的 run 会撞文件，
    # 测试里强制唯一。
    _seed_run.counter += 1
    run["run_id"] = f"{run['run_id']}-{_seed_run.counter}"
    svc.save_review_run(run)
    return run


_seed_run.counter = 0


def test_list_empty(api_client):
    r = api_client.get("/api/reviews/runs")
    assert r.status_code == 200, r.text
    assert r.json() == {"items": []}


def test_list_filter_and_order(api_client):
    r1 = _seed_run(session_key="key-1", project_id="proj-1")
    r2 = _seed_run(session_key="key-2", project_id="proj-1")
    r3 = _seed_run(session_key="key-1", project_id="proj-2")
    # 列表按文件 mtime 倒序：显式设置 mtime 保证排序确定性
    import os

    base = 1_700_000_000.0
    for i, r in enumerate((r1, r2, r3)):
        p = svc._run_path(r["run_id"])
        os.utime(p, (base + i, base + i))

    r = api_client.get("/api/reviews/runs")
    assert r.status_code == 200
    ids = [it["run_id"] for it in r.json()["items"]]
    assert ids == [r3["run_id"], r2["run_id"], r1["run_id"]]

    r = api_client.get("/api/reviews/runs", params={"session_key": "key-1"})
    assert {it["run_id"] for it in r.json()["items"]} == {
        r1["run_id"],
        r3["run_id"],
    }
    r = api_client.get("/api/reviews/runs", params={"project_id": "proj-2"})
    assert [it["run_id"] for it in r.json()["items"]] == [r3["run_id"]]
    r = api_client.get("/api/reviews/runs", params={"limit": 1})
    assert len(r.json()["items"]) == 1


def test_detail_ok_with_counts_and_stale_defaults(api_client):
    run = _seed_run(session_key="key-9", outcome="flagged")
    svc.save_dispositions(
        "key-9",
        {
            "note-a": {"status": "warning", "reflag_count": 1, "disposition": "open"},
            "note-b": {"status": "failure", "reflag_count": 2, "disposition": "unaddressed"},
            "note-c": {"status": "pass", "reflag_count": 0, "disposition": "resolved"},
        },
    )
    r = api_client.get(f"/api/reviews/runs/{run['run_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == run["run_id"]
    assert body["warn_count"] == 1
    assert body["fail_count"] == 1
    assert body["unaddressed_count"] == 1
    # W5-3 stale contract：无 scope 绑定的 legacy run → "unverified"（诚实标记）
    assert body["stale"] == "unverified"
    assert "legacy" in (body["stale_reason"] or "")
    assert set(body["dispositions"]) == {"note-a", "note-b", "note-c"}


def test_detail_artifact_scope_stale_detection(api_client, monkeypatch):
    run = _seed_run(
        scope_kind="artifact",
        scope_digest="stored-aaa",
        artifact_version_id="v-1",
    )
    # 内容变更 → stale=True
    monkeypatch.setattr(svc, "_artifact_content_digest", lambda _vid: "current-bbb")
    r = api_client.get(f"/api/reviews/runs/{run['run_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["stale"] is True
    assert "changed" in (body["stale_reason"] or "")
    # 内容一致 → stale=False
    monkeypatch.setattr(svc, "_artifact_content_digest", lambda _vid: "stored-aaa")
    r = api_client.get(f"/api/reviews/runs/{run['run_id']}")
    assert r.json()["stale"] is False
    assert r.json()["stale_reason"] is None


def test_detail_404(api_client):
    r = api_client.get("/api/reviews/runs/no-such-run")
    assert r.status_code == 404


def test_rerun_404(api_client):
    r = api_client.post(
        "/api/reviews/runs/no-such-run/rerun",
        json={"question": "q", "answer": "a"},
    )
    assert r.status_code == 404


def test_rerun_400_missing_inputs(api_client):
    run = _seed_run()
    r = api_client.post(
        f"/api/reviews/runs/{run['run_id']}/rerun", json={"question": "", "answer": "a"}
    )
    assert r.status_code == 400


def test_rerun_ok_reuses_fix_loop(api_client, monkeypatch):
    run = _seed_run(project_id="proj-1")
    calls = {}

    def fake_review_answer(question, answer, citations, *, settings):
        calls["review_args"] = (question, answer, citations)
        return {"status": "warning", "notes": ["n1"]}

    def fake_run_fix_loop(*, question, answer, citations, review, settings,
                          repair_fn, max_rounds, project_id):
        calls["fix_args"] = {
            "question": question,
            "max_rounds": max_rounds,
            "project_id": project_id,
        }
        assert callable(repair_fn)
        return "final-a", {"run_id": "new-run-1", "rounds": 0}

    import app.services.evidence_reviewer as reviewer_mod

    monkeypatch.setattr(reviewer_mod, "review_answer", fake_review_answer)
    monkeypatch.setattr(svc, "run_fix_loop", fake_run_fix_loop)
    monkeypatch.setattr(api_mod, "get_settings", lambda: SimpleNamespace())

    r = api_client.post(
        f"/api/reviews/runs/{run['run_id']}/rerun",
        json={"question": "q?", "answer": "a.", "citations": [], "max_rounds": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_answer"] == "final-a"
    assert body["review"]["status"] == "warning"
    assert body["fix"]["run_id"] == "new-run-1"
    assert calls["review_args"] == ("q?", "a.", [])
    assert calls["fix_args"]["max_rounds"] == 2
    assert calls["fix_args"]["project_id"] == "proj-1"
