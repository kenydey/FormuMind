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
