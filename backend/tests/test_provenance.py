"""W2-6 (P1-2): provenance graph + lineage; P2-10 reserved-field write protection."""
from __future__ import annotations

import logging

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.services import provenance
from app.services.provenance import (
    claim_id_for_text,
    count_edges,
    ensure_provenance,
    formulation_id_for,
    lineage,
    link,
    record_claim_sources,
)


@pytest.fixture()
def sf(tmp_path):
    db_path = tmp_path / "provenance_test.db"
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def store(sf):
    return SourceStore(sf)


def _broken_sf():
    def _boom():
        raise RuntimeError("db down")

    return _boom


# ── table / link ──────────────────────────────────────────────────────────────


def test_ensure_creates_empty_graph(sf):
    ensure_provenance(sf)
    assert count_edges(session_factory=sf) == 0


def test_link_records_edge(sf):
    ensure_provenance(sf)
    assert link("claim", "claim:abc", "source", "src-1", session_factory=sf) is True
    assert count_edges(session_factory=sf) == 1


def test_link_is_idempotent(sf):
    ensure_provenance(sf)
    link("claim", "claim:abc", "source", "src-1", "cites", session_factory=sf)
    link("claim", "claim:abc", "source", "src-1", "cites", session_factory=sf)
    assert count_edges(session_factory=sf) == 1


def test_link_rejects_unknown_node_type(sf):
    with pytest.raises(ValueError):
        link("bogus", "x", "source", "src-1", session_factory=sf)
    with pytest.raises(ValueError):
        link("claim", "x", "bogus", "src-1", session_factory=sf)


def test_link_rejects_empty_node_id(sf):
    with pytest.raises(ValueError):
        link("claim", "", "source", "src-1", session_factory=sf)


def test_link_fail_open_on_db_error():
    assert link("claim", "c1", "source", "s1", session_factory=_broken_sf()) is False


def test_link_self_heals_missing_table(sf, tmp_path):
    # Fresh DB file without ensure_* called: link() must bootstrap the table.
    engine = make_engine(f"sqlite:///{tmp_path}/fresh.db")
    fresh_sf = make_session_factory(engine)
    assert link("run", "r1", "formulation", "f1", session_factory=fresh_sf) is True
    assert count_edges(session_factory=fresh_sf) == 1


# ── lineage ───────────────────────────────────────────────────────────────────


def test_lineage_single_hop(sf):
    ensure_provenance(sf)
    link("claim", "claim:1", "source", "src-1", "cites", session_factory=sf)
    edges = lineage("source", "src-1", session_factory=sf)
    assert len(edges) == 1
    assert edges[0]["from_type"] == "claim"
    assert edges[0]["relation"] == "cites"


def test_lineage_multi_hop_formulation_to_source(sf):
    ensure_provenance(sf)
    link("formulation", "f1", "claim", "claim:1", "summarised_by", session_factory=sf)
    link("claim", "claim:1", "source", "src-1", "cites", session_factory=sf)
    link("run", "run-9", "formulation", "f1", "tests", session_factory=sf)
    edges = lineage("source", "src-1", depth=3, session_factory=sf)
    pairs = {(e["from_type"], e["to_type"]) for e in edges}
    assert ("claim", "source") in pairs
    assert ("formulation", "claim") in pairs
    # run -> formulation -> claim -> source: run is upstream of the source too
    assert ("run", "formulation") in pairs
    assert len(edges) == 3


def test_lineage_respects_depth(sf):
    ensure_provenance(sf)
    link("formulation", "f1", "claim", "claim:1", session_factory=sf)
    link("claim", "claim:1", "source", "src-1", session_factory=sf)
    edges = lineage("source", "src-1", depth=1, session_factory=sf)
    assert len(edges) == 1
    assert edges[0]["from_type"] == "claim"


def test_lineage_cycle_safe(sf):
    ensure_provenance(sf)
    link("claim", "claim:a", "source", "src-x", session_factory=sf)
    link("source", "src-x", "claim", "claim:a", session_factory=sf)  # back edge
    edges = lineage("source", "src-x", depth=5, session_factory=sf)
    assert len(edges) == 2  # terminates, no duplicates


def test_lineage_unknown_node_returns_empty(sf):
    ensure_provenance(sf)
    assert lineage("source", "no-such-id", session_factory=sf) == []


def test_lineage_fail_open_on_db_error():
    assert lineage("source", "s1", session_factory=_broken_sf()) == []


def test_lineage_rejects_bad_node_type(sf):
    with pytest.raises(ValueError):
        lineage("bogus", "x", session_factory=sf)


# ── helpers ───────────────────────────────────────────────────────────────────


def test_claim_id_stable():
    assert claim_id_for_text("hello") == claim_id_for_text("hello")
    assert claim_id_for_text("hello") != claim_id_for_text("world")
    assert claim_id_for_text("hello").startswith("claim:")


def test_record_claim_sources_links_and_dedupes(sf):
    ensure_provenance(sf)
    cid = record_claim_sources("some claim", ["s1", "s2", "s1"], session_factory=sf)
    assert cid == claim_id_for_text("some claim")
    edges = lineage("source", "s1", session_factory=sf)
    assert len(edges) == 1
    assert edges[0]["from_id"] == cid
    assert count_edges(session_factory=sf) == 2


def test_formulation_id_stable_by_composition():
    class Ing:
        def __init__(self, name, pct):
            self.name = name
            self.weight_pct = pct

    class F:
        def __init__(self, ings):
            self.ingredients = ings

    a = F([Ing("环氧树脂", 60.0), Ing("固化剂", 40.0)])
    b = F([Ing("固化剂", 40), Ing("环氧树脂", 60.0)])  # order-insensitive
    c = F([Ing("环氧树脂", 61.0), Ing("固化剂", 39.0)])
    assert formulation_id_for(a) == formulation_id_for(b)
    assert formulation_id_for(a) != formulation_id_for(c)
    assert formulation_id_for(a).startswith("formulation:")


# ── P2-10: reserved-field write protection ────────────────────────────────────


def _make_source(store: SourceStore) -> str:
    return store.create(
        filename="t.pdf",
        title="t",
        source_kind="local",
        full_text="text",
        content_hash="hash-1",
        origin_url="https://example.com/t.pdf",
    )


def test_update_fields_allows_ordinary_field(store):
    sid = _make_source(store)
    assert store.update_fields(sid, title="new title") is True
    assert store.get(sid).title == "new title"


def test_update_fields_missing_row(store):
    assert store.update_fields("no-such-id", title="x") is False


def test_update_fields_rejects_content_hash(store, caplog):
    sid = _make_source(store)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError, match="write-protected"):
            store.update_fields(sid, content_hash="tampered")
    assert "content_hash" in caplog.text
    assert store.get(sid).content_hash == "hash-1"  # unchanged


def test_update_fields_rejects_origin_url(store, caplog):
    sid = _make_source(store)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError, match="write-protected"):
            store.update_fields(sid, origin_url="https://evil.example/")
    assert store.get(sid).origin_url == "https://example.com/t.pdf"


def test_update_fields_rejects_alias_and_future_fields(store):
    sid = _make_source(store)
    with pytest.raises(ValueError, match="write-protected"):
        store.update_fields(sid, source_url="https://evil.example/")
    with pytest.raises(ValueError, match="write-protected"):
        store.update_fields(sid, retrieved_at="2026-01-01")


def test_update_fields_rejects_unknown_field(store):
    sid = _make_source(store)
    with pytest.raises(ValueError, match="unknown SourceDocument"):
        store.update_fields(sid, no_such_column="x")
