"""P1-6: auto_loop_max_rounds is enforced (not just persisted)."""
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore


def _store(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return ProjectStore(make_session_factory(engine))


def test_consume_round_increments(tmp_path):
    store = _store(tmp_path)
    detail = store.create(title="loop-test")
    pid = detail.id
    store.update(pid, {"auto_loop_on_sync": True,
                       "auto_loop_max_rounds": 2,
                       "auto_loop_round": 0})
    ok, _ = store.try_consume_auto_loop_round(pid)
    assert ok is True
    ok, _ = store.try_consume_auto_loop_round(pid)
    assert ok is True
    # third exceeds max_rounds=2
    ok, reason = store.try_consume_auto_loop_round(pid)
    assert ok is False
    assert "上限" in reason
    # counter stays at max (not incremented past)
    d = store.get(pid)
    assert d.workspace.auto_loop_round == 2


def test_consume_missing_project(tmp_path):
    store = _store(tmp_path)
    ok, _ = store.try_consume_auto_loop_round("no-such-id")
    assert ok is False


def test_paused_campaign_does_not_consume_round(monkeypatch):
    """v13-5: 暂停的 campaign 不得白烧轮数（consume 在 bail-out 之后）。"""
    import app.services.workbench_loop as wl

    calls = []
    monkeypatch.setattr(wl, "should_trigger_loop_after_sync", lambda *a, **k: True)
    monkeypatch.setattr(wl, "is_doecycle_paused", lambda cid: True)

    class FakeStore:
        def try_consume_auto_loop_round(self, pid):
            calls.append(pid)
            return True, ""

    monkeypatch.setattr(
        "app.db.project_store.get_project_store", lambda: FakeStore()
    )

    task_id, msg = wl.dispatch_loop_after_sync(
        training_ingested=1,
        workbench_campaign_id=1,
        project_id="p1",
    )
    assert task_id is None
    assert "暂停" in msg
    assert calls == [], "暂停时不得消耗轮数"


def test_converged_campaign_does_not_consume_round(monkeypatch):
    """v13-5: 已收敛的 campaign 不得白烧轮数。"""
    import app.services.workbench_loop as wl

    calls = []
    monkeypatch.setattr(wl, "should_trigger_loop_after_sync", lambda *a, **k: True)
    monkeypatch.setattr(wl, "is_doecycle_paused", lambda cid: False)
    monkeypatch.setattr(
        wl, "_campaign_loop_context", lambda cid: ([], True, "已收敛")
    )

    class FakeStore:
        def try_consume_auto_loop_round(self, pid):
            calls.append(pid)
            return True, ""

    monkeypatch.setattr(
        "app.db.project_store.get_project_store", lambda: FakeStore()
    )

    task_id, msg = wl.dispatch_loop_after_sync(
        training_ingested=1,
        workbench_campaign_id=1,
        project_id="p1",
    )
    assert task_id is None
    assert calls == [], "收敛时不得消耗轮数"
