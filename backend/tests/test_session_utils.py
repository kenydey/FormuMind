"""commit_session rollback helper."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.db.session_utils import commit_session


def test_commit_session_commits_on_success():
    session = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session

    with commit_session(factory) as s:
        s.add("row")

    session.commit.assert_called_once()
    session.rollback.assert_not_called()


def test_commit_session_rolls_back_on_error():
    session = MagicMock()
    session.commit.side_effect = RuntimeError("db down")
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session

    with pytest.raises(RuntimeError, match="db down"):
        with commit_session(factory) as s:
            s.add("row")

    session.rollback.assert_called_once()


def test_commit_session_retries_on_database_locked(monkeypatch):
    """commit_session retries commit on SQLite 'database is locked' (P0 fix)."""
    from sqlalchemy.exc import OperationalError

    from app.db.session_utils import commit_session

    monkeypatch.setattr("app.db.session_utils.time.sleep", lambda s: None)

    session = MagicMock()
    session.commit.side_effect = [
        OperationalError("stmt", {}, Exception("database is locked")),
        OperationalError("stmt", {}, Exception("database is locked")),
        None,
    ]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session

    with commit_session(factory) as s:
        s.add("row")

    assert session.commit.call_count == 3
    # 锁重试前需 rollback 清 PendingRollbackError 态（session_utils 注释），
    # 故前两次失败各触发一次 rollback，最终 commit 成功。
    assert session.rollback.call_count == 2
