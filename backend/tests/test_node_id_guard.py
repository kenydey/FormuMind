"""A parametrized test whose parameter is a huge string must carry an explicit id.

pytest stores the running test's node id in ``os.environ["PYTEST_CURRENT_TEST"]`` and Windows refuses an environment
variable longer than 32,767 characters, so ``@pytest.mark.parametrize("text", ["1" * 40_000])`` is a setup error there
(the ReDoS tests added in round 4 were red on `backend-windows` for exactly this) and perfectly fine on Linux. The
suite's conftest refuses to collect such a test on every platform; these pin that it does.
"""
from __future__ import annotations

import pytest

from tests import conftest as suite


class _Item:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


def test_ordinary_node_ids_pass():
    items = [_Item("tests/test_x.py::test_y[" + "a" * 300 + "]"), _Item("tests/test_x.py::test_z")]
    assert suite.overlong_node_ids(items) == []
    suite.pytest_collection_modifyitems(None, items)  # does not raise


def test_a_node_id_with_a_huge_parameter_aborts_collection_and_names_it():
    items = [_Item("tests/test_x.py::test_fine"), _Item("tests/test_x.py::test_huge[" + "1" * 40_000 + "]")]
    assert len(suite.overlong_node_ids(items)) == 1
    with pytest.raises(pytest.UsageError) as caught:
        suite.pytest_collection_modifyitems(None, items)
    message = str(caught.value)
    assert "test_huge" in message and "pytest.param" in message and "40" in message
    assert "test_fine" not in message
    assert len(message) < 1000, "the error must not echo the whole parameter"


def test_the_limit_is_far_below_what_windows_allows():
    assert suite.MAX_NODEID_CHARS * 4 < 32_767
