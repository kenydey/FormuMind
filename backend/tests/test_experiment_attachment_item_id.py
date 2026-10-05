"""Attachments uploaded to an experiment must resolve the experiment's Datalab sample (round-4).

``_datalab_item_id_for`` wrote ``with default_session_factory() as session`` — a ``sessionmaker`` is not a
context manager. The ``TypeError`` landed in ``except Exception: logger.warning(...)`` and the helper
returned ``None`` for every experiment, so uploads never attached to their sample. The only existing test
patched the helper out entirely.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.api import experiments
from app.config import get_settings
from app.db.database import Base, default_engine, default_session_factory
from app.db.models import ExperimentRow


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/item.db")
    get_settings.cache_clear()
    Base.metadata.create_all(default_engine())
    yield
    get_settings.cache_clear()


def _add(item_id: str | None) -> int:
    with default_session_factory()() as session:
        row = ExperimentRow(domain="anticorrosion_coating", factors={}, measured={}, item_id=item_id)
        session.add(row)
        session.commit()
        return row.id


def test_an_experiment_on_a_datalab_sample_resolves_to_it(db, caplog):
    experiment_id = _add("formumind_c3_r7_abc123")
    with caplog.at_level(logging.WARNING, logger=experiments.logger.name):
        item_id = asyncio.run(experiments._datalab_item_id_for(experiment_id=experiment_id))
    assert item_id == "formumind_c3_r7_abc123"
    # a swallowed TypeError would show up here even if some other fallback produced a value
    assert "item_id resolve failed" not in caplog.text


def test_an_experiment_without_a_sample_or_an_unknown_one_resolves_to_none(db):
    assert asyncio.run(experiments._datalab_item_id_for(experiment_id=_add(None))) is None
    assert asyncio.run(experiments._datalab_item_id_for(experiment_id=999_999)) is None
    assert asyncio.run(experiments._datalab_item_id_for()) is None


def test_the_lookup_runs_off_the_event_loop(db, monkeypatch):
    """It reads SQLite (and, for workbench rows, a campaign store): not on the loop."""
    import threading

    seen: dict[str, str] = {}

    def spy(experiment_id: int, campaign_id: int, row_id: int):
        seen["thread"] = threading.current_thread().name
        return "x"

    monkeypatch.setattr(experiments, "_resolve_datalab_item_id", spy)
    main = threading.current_thread().name
    assert asyncio.run(experiments._datalab_item_id_for(experiment_id=1)) == "x"
    assert seen["thread"] != main
