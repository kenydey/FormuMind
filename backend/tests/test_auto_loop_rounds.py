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
