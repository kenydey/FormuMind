"""topic_sweep 主题雷达测试(2026-09-05 P2)."""
from app.domain.schemas import Evidence


def _ev(identifier: str, source: str = "OpenAlex") -> Evidence:
    return Evidence(
        source=source,
        identifier=identifier,
        title=f"Doc {identifier}",
        snippet="abstract only",
        relevance=0.9,
    )


def test_topic_sweep_searches_and_dispatches(monkeypatch):
    from app.worker.tasks import run_topic_sweep

    called = {}

    def fake_iter(query, source_types, req=None, total_limit=0, per_source_cap=0, progress_cb=None):
        called["query"] = query
        called["source_types"] = source_types
        return ([_ev("10.1000/example")], {"kept": 1, "dropped": 0})

    def fake_dispatch(ev_dicts, project_id=None, query=None):
        called["project_id"] = project_id
        called["query_dispatch"] = query
        assert len(ev_dicts) == 1
        return "kb-task-1"

    monkeypatch.setattr("app.services.literature.iter_search", fake_iter)
    monkeypatch.setattr("app.worker.tasks.dispatch_kb_ingest", fake_dispatch)

    res = run_topic_sweep.delay(
        {"query": "镁合金 无铬钝化", "project_id": "1d10717c", "total_limit": 50}
    ).get()

    assert res["found"] == 1
    assert res["ingest_task_id"] == "kb-task-1"
    assert called["query"] == "镁合金 无铬钝化"
    assert called["project_id"] == "1d10717c"


def test_topic_sweep_empty_result_skips_ingest(monkeypatch):
    from app.worker.tasks import run_topic_sweep

    def fake_iter(query, source_types, req=None, total_limit=0, per_source_cap=0, progress_cb=None):
        return ([], {})

    monkeypatch.setattr("app.services.literature.iter_search", fake_iter)
    res = run_topic_sweep.delay({"query": "nonexistent chemistry", "project_id": None}).get()
    assert res["found"] == 0
    assert res["ingest_task_id"] is None
