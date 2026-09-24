"""W2: topicality enforce when kb_relevance_shadow=False (default stays shadow)."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import kb_ingest


@pytest.fixture(autouse=True)
def _fresh():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _rows():
    return [
        Evidence(
            source="OpenAlex",
            identifier="10.1000/on",
            title="Magnesium alloy passivation chrome-free",
            snippet="conversion coating for magnesium",
            relevance=0.99,
        ),
        Evidence(
            source="OpenAlex",
            identifier="10.1000/off",
            title="Quantum computing qubit entanglement",
            snippet="superconducting circuits",
            relevance=0.98,
        ),
    ]


def test_shadow_default_still_keeps_off_topic(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_relevance_shadow", True, raising=False)
    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.45, raising=False)

    targets = kb_ingest.select_ingest_targets(
        _rows(),
        project_id=None,
        query="magnesium passivation chrome-free",
        skip_topic_filter=True,
        write_audit=False,
    )
    ids = {ev.identifier for ev, _ in targets}
    assert ids == {"10.1000/on", "10.1000/off"}


def test_enforce_drops_low_topicality(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_relevance_shadow", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.45, raising=False)

    targets = kb_ingest.select_ingest_targets(
        _rows(),
        project_id=None,
        query="magnesium passivation chrome-free",
        skip_topic_filter=True,
        write_audit=False,
    )
    ids = {ev.identifier for ev, _ in targets}
    assert ids == {"10.1000/on"}


def test_enforce_skips_when_query_has_no_keywords(monkeypatch):
    """Empty/stopword-only query must not reject every row (topicality=0)."""
    monkeypatch.setattr(get_settings(), "kb_relevance_shadow", False, raising=False)
    monkeypatch.setattr(get_settings(), "kb_project_source_quota", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.45, raising=False)

    targets = kb_ingest.select_ingest_targets(
        _rows(),
        project_id=None,
        query="",
        skip_topic_filter=True,
        write_audit=False,
    )
    assert len(targets) == 2


def test_kb_relevance_shadow_registered_in_env_flags():
    from app.services.env_flags import FLAG_REGISTRY

    attrs = {f.attr for f in FLAG_REGISTRY}
    assert "kb_relevance_shadow" in attrs
