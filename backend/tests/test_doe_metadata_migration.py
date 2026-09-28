"""Regression tests for one-time ``clean_doe_metadata_from_factors`` migration.

The cleanup used to full-scan the ``experiments`` table on every registry
startup. It now writes a ``fm_data_migrations`` marker in the same transaction
as the scrub, so subsequent runs skip the scan entirely while the scrub
itself stays idempotent.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import migrate


def _make_db(tmp_path, monkeypatch):
    """Point FORMUMIND_DB_URL at a throwaway sqlite file; return factory."""
    db_file = tmp_path / "mig_test.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{db_file}")
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow

    factory = default_session_factory()
    engine = factory.kw["bind"]
    ExperimentRow.__table__.create(engine, checkfirst=True)
    return factory


def _seed(factory):
    from app.db.models import ExperimentRow

    dirty1 = {"temp": 25.0, "_doe_metadata": {"cycle": 1, "plan": [1, 2]}}
    dirty2 = {"temp": "30", "bad": [1, 2, 3], "_doe_metadata": {"x": 1}}
    clean = {"temp": 20.0, "pressure": 1.5}
    with factory() as session:
        session.add_all(
            [
                ExperimentRow(domain="d", factors=dirty1, measured={}),
                ExperimentRow(domain="d", factors=dirty2, measured={}),
                ExperimentRow(domain="d", factors=clean, measured={}),
            ]
        )
        session.commit()


def _all_factors(factory):
    from app.db.models import ExperimentRow

    with factory() as session:
        return [r.factors for r in session.query(ExperimentRow).order_by(ExperimentRow.id)]


def test_first_run_cleans_dirty_rows(tmp_path, monkeypatch):
    factory = _make_db(tmp_path, monkeypatch)
    _seed(factory)

    cleaned = migrate.clean_doe_metadata_from_factors()

    assert cleaned == 2
    factors = _all_factors(factory)
    assert factors[0] == {"temp": 25.0}
    assert factors[1] == {"temp": 30.0}
    assert factors[2] == {"temp": 20.0, "pressure": 1.5}


def test_second_run_skips_without_scanning(tmp_path, monkeypatch):
    factory = _make_db(tmp_path, monkeypatch)
    _seed(factory)
    assert migrate.clean_doe_metadata_from_factors() == 2

    calls = []
    real_query = Session.query

    def counting_query(self, *args, **kwargs):
        calls.append(args)
        return real_query(self, *args, **kwargs)

    monkeypatch.setattr(Session, "query", counting_query)

    assert migrate.clean_doe_metadata_from_factors() == 0
    # 标记命中直接返回：experiments 表未被全表扫描（无 session.query 调用）
    assert calls == []


def test_rerun_after_marker_loss_is_idempotent(tmp_path, monkeypatch):
    factory = _make_db(tmp_path, monkeypatch)
    _seed(factory)
    assert migrate.clean_doe_metadata_from_factors() == 2
    before = _all_factors(factory)

    # 模拟标记丢失（表被删/标记行被删）：重跑不得破坏已清洗数据
    with factory() as session:
        session.execute(migrate._data_migration_table().delete())
        session.commit()

    assert migrate.clean_doe_metadata_from_factors() == 0
    assert _all_factors(factory) == before


def test_marker_written_in_same_transaction(tmp_path, monkeypatch):
    """标记与清洗同事务：崩溃回滚后下次仍会执行（而非跳过）。"""
    factory = _make_db(tmp_path, monkeypatch)
    _seed(factory)

    orig_execute = Session.execute

    def failing_execute(self, clause, *args, **kwargs):
        # 让 marker 的 INSERT 抛错，模拟"洗了一半崩了"
        if isinstance(clause, type(migrate._data_migration_table().insert())):
            raise RuntimeError("simulated crash before commit")
        return orig_execute(self, clause, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", failing_execute)
    try:
        migrate.clean_doe_metadata_from_factors()
    except RuntimeError:
        pass
    finally:
        # 只恢复 execute 补丁，保留 FORMUMIND_DB_URL 指向同一测试库
        monkeypatch.setattr(Session, "execute", orig_execute)

    # 事务已回滚：标记不存在，脏数据未动
    with factory() as session:
        assert (
            session.execute(migrate._data_migration_table().select()).first() is None
        )
    factors = _all_factors(factory)
    assert any("_doe_metadata" in f for f in factors)

    # 下次重跑正常执行并清洗
    assert migrate.clean_doe_metadata_from_factors() == 2
    factors = _all_factors(factory)
    assert all("_doe_metadata" not in f for f in factors)
