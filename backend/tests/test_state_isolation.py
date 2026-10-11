"""Process-global state must not leak from one test into the next.

conftest's autouse fixture resets the shared singletons; these pairs prove each reset is wired. A pair is two tests
in file order - the first dirties the state and the second asserts it starts clean - so deleting a reset from
conftest fails the second test, instead of surfacing months later as an unrelated test that only fails when another
file happens to run before it (the suite runs in alphabetical order, which hides every such dependency).
"""
from __future__ import annotations

from app.services.runtime_secrets import get_runtime_secrets


def test_leak_runtime_secret_part_one() -> None:
    get_runtime_secrets().set("mineru_api_key", None)
    assert get_runtime_secrets().snapshot() == {"mineru_api_key": None}


def test_leak_runtime_secret_part_two_starts_clean() -> None:
    assert get_runtime_secrets().snapshot() == {}


def test_leak_embedding_breaker_part_one() -> None:
    from app.services import kb_index

    kb_index._EMBED_FAIL_COUNT["some-model"] = 99
    kb_index._EMBED_BROKEN["some-model"] = 1.0
    assert kb_index._EMBED_BROKEN


def test_leak_embedding_breaker_part_two_starts_clean() -> None:
    from app.services import kb_index

    assert kb_index._EMBED_BROKEN == {} and kb_index._EMBED_FAIL_COUNT == {}
