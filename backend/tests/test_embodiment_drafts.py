"""P3.1 embodiment drafts — eligibility, table wt%, confirm red lines."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.source_store import SourceStore
from app.services import embodiment_drafts as emb


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_mod
    import app.db.entity_store as entity_mod
    import app.db.source_store as source_mod

    engine = make_engine(f"sqlite:///{tmp_path}/emb_p31.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    sources = SourceStore(factory)
    chunks = ChunkStore(factory)
    entities = EntityStore(factory)
    monkeypatch.setattr(source_mod, "_store", sources)
    monkeypatch.setattr(chunk_mod, "_store", chunks)
    monkeypatch.setattr(entity_mod, "_store", entities)
    return sources, chunks, entities


def _seed_doc(sources: SourceStore, chunks: ChunkStore, *, text: str, status="ok", kind="patent", origin_url=None):
    sid = sources.create(
        filename="demo.pdf",
        title="Zinc phosphate epoxy primer",
        source_kind=kind,
        full_text=text,
        content_hash=f"h-{hash(text) & 0xFFFFFFFF:x}",
        extraction_status=status,
        origin_url=origin_url or "https://patents.google.com/patent/CN104789083B",
    )
    # Simulate prune: clear full_text but keep chunks + raw_text_chars
    chunk_rows = [{"text": text, "heading_path": "Example 1", "page_no": 3, "meta": {}}]
    with chunks._session_factory() as session:
        chunks.replace_for_source_in(session, sid, chunk_rows)
        session.commit()
    sources.clear_full_text(sid)
    return sid


def test_eligibility_not_ingested(stores):
    out = emb.check_eligibility("missing-id")
    assert out["eligible"] is False
    assert out["reason"] == "not_ingested"


def test_eligibility_too_short(stores):
    sources, chunks, _ = stores
    sid = sources.create(
        filename="a.pdf",
        title="short",
        source_kind="patent",
        full_text="tiny",
        content_hash="h1",
        extraction_status="ok",
    )
    out = emb.check_eligibility(sid)
    assert out["eligible"] is False
    assert out["reason"] == "too_short"


def test_eligibility_failed(stores):
    sources, chunks, _ = stores
    text = "x" * 600
    sid = sources.create(
        filename="a.pdf",
        title="fail",
        source_kind="patent",
        full_text=text,
        content_hash="h2",
        extraction_status="failed",
    )
    out = emb.check_eligibility(sid)
    assert out["eligible"] is False
    assert out["reason"] == "extract_failed"


def test_eligibility_ok_with_chunks_after_prune(stores):
    sources, chunks, _ = stores
    text = ("Epoxy resin formulation table follows.\n" * 40) + "| Component | wt% |\n"
    sid = _seed_doc(sources, chunks, text=text)
    out = emb.check_eligibility(sid)
    assert out["eligible"] is True
    assert out["chunk_count"] >= 1
    assert out["reason"] is None


def test_parse_markdown_table_to_real_wt():
    md = """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 40 |
| Zinc phosphate | 25 |
| Solvent | 35 |
"""
    tables = emb.parse_markdown_tables(md)
    assert len(tables) == 1
    emb_row = emb.table_to_ingredients(tables[0])
    assert emb_row is not None
    assert emb_row["amount_source"] == "table"
    pcts = [i["weight_pct"] for i in emb_row["ingredients"]]
    assert pcts == [40.0, 25.0, 35.0]
    assert pcts != [round(100 / 3, 4)] * 3


def test_parts_normalized():
    md = """
| Ingredient | parts |
| --- | --- |
| Resin | 50 |
| Hardener | 30 |
| Pigment | 20 |
"""
    tables = emb.parse_markdown_tables(md)
    emb_row = emb.table_to_ingredients(tables[0])
    assert emb_row["amount_source"] == "table"
    assert sum(i["weight_pct"] for i in emb_row["ingredients"]) == pytest.approx(100.0)


def test_extract_and_confirm(stores, monkeypatch):
    sources, chunks, entities = stores
    md = (
        "Patent description with enough characters for eligibility gate. " * 20
        + """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 55 |
| Zinc phosphate | 15 |
| Talc | 30 |
"""
    )
    sid = _seed_doc(sources, chunks, text=md, origin_url="https://patents.google.com/patent/CN104789083B")

    proposed = []

    def _fake_propose(name, spec=None, *, source="kb_promoted", source_ref="", force_pending=False):
        assert force_pending is True
        proposed.append(name)
        return {"action": "pending", "name": name}

    monkeypatch.setattr(emb, "propose_material", _fake_propose)

    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["ok"] is True
    draft = out["draft"]
    assert draft["needs_review"] is True
    assert draft["source_id"] == sid
    assert draft["amount_source"] == "table"
    assert draft.get("text_provenance") in {"markdown_table", "html_table", "prose", "none"}
    assert draft["origin"] == "patent_fulltext"
    pcts = [i["weight_pct"] for i in draft["formulation"]["ingredients"]]
    assert 55.0 in pcts

    conf = emb.confirm_embodiment_draft(draft)
    assert conf["ok"] is True
    assert conf["promoted_to_pool"] is False
    assert proposed
    assert entities.get_entity(conf["formulation_entity_id"]) is not None
    assert conf["document_entity_id"] and conf["document_entity_id"].startswith("patent:")


def test_extract_rejects_ineligible(stores):
    out = emb.extract_embodiment_draft(source_id="nope")
    assert out["ok"] is False
    assert out["reason"] == "not_ingested"


def test_api_routes(stores, monkeypatch):
    sources, chunks, _ = stores
    md = (
        "Enough body text for the eligibility character threshold here. " * 15
        + """
| Component | wt% |
| --- | --- |
| Resin | 70 |
| Pigment | 30 |
"""
    )
    sid = _seed_doc(sources, chunks, text=md)
    monkeypatch.setattr(
        emb,
        "propose_material",
        lambda *a, **k: {"action": "pending", "name": a[0] if a else "x"},
    )

    from app.main import app

    client = TestClient(app)
    el = client.post("/api/formulations/embodiment-eligibility", json={"source_ids": [sid]})
    assert el.status_code == 200
    assert el.json()["items"][0]["eligible"] is True

    bad = client.post("/api/formulations/extract-embodiment-draft", json={"source_id": "missing"})
    assert bad.status_code in (400, 404)

    ex = client.post("/api/formulations/extract-embodiment-draft", json={"source_id": sid})
    assert ex.status_code == 200
    draft = ex.json()["draft"]
    assert draft["amount_source"] == "table"

    conf = client.post("/api/formulations/confirm-embodiment-draft", json={"draft": draft})
    assert conf.status_code == 200
    assert conf.json()["promoted_to_pool"] is False


def test_normalize_patent_pub():
    office, compact = emb.normalize_patent_pub("https://patents.google.com/patent/CN-104789083-B")
    assert office == "CN"
    assert compact == "CN104789083B"
    assert emb.patent_entity_id_from_pub("CN104789083B") == "patent:cn:CN104789083B"


def test_f0_no_title_case_noise_ingredients(stores):
    """CN1227312C-style English boilerplate must not become fake ingredients."""
    sources, chunks, _ = stores
    prose = (
        "Translated from the original. Typical compositions may include silane. "
        "The coating composition may further comprise additives. "
        "Coatings for food and beverage packaging. The primer composition includes "
        "a binder. Description of the invention follows with enough filler text. "
    ) * 12
    sid = _seed_doc(sources, chunks, text=prose, origin_url="CN1227312C")
    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["ok"] is True
    draft = out["draft"]
    assert draft["amount_source"] == "placeholder"
    names = [i["name"] for i in draft["formulation"]["ingredients"]]
    assert names == []
    assert draft["chemistry_count"] == 0
    joined = " ".join(draft["formulation"]["warnings"])
    assert "未识别到可用配方表" in joined
    assert "Typical compositions" not in joined
    for bad in (
        "Typical compositions may include",
        "Translated from",
        "The coating composition may",
        "Coatings for food and",
    ):
        assert bad not in names

    conf = emb.confirm_embodiment_draft(draft)
    assert conf["ok"] is False
    assert conf["reason"] == "no_usable_ingredients"


def test_f3_flattened_rows_recovery():
    text = """
Example 1
Epoxy resin 40
Zinc phosphate 25
Solvent 35
"""
    flat = emb.parse_flattened_amount_rows(text)
    assert flat is not None
    assert flat["amount_source"] == "prose"
    pcts = [i["weight_pct"] for i in flat["ingredients"]]
    assert pcts == [40.0, 25.0, 35.0]


def test_f3_rejects_boilerplate_as_rows():
    text = """
Typical compositions may include 10
The coating composition may 20
"""
    assert emb.parse_flattened_amount_rows(text) is None


def test_f3_dirty_name_lowers_confidence_and_warns():
    text = """
Example 2
Water and its preparation method 40
Epoxy resin 60
"""
    flat = emb.parse_flattened_amount_rows(text)
    assert flat is not None
    dirty = next(i for i in flat["ingredients"] if "preparation" in i["name"].lower())
    assert dirty["confidence"] <= 0.45
    assert any("标题污染" in w or "名称可能" in w for w in flat["warnings"])


def test_citation_table_rejected():
    md = """
| Patent | Title | Year |
| --- | --- | --- |
| DE102011120870B4 | Prior art coating | 2012 |
| CN102528001A | Magnesium alloy | 2011 |
| US8608869B2 | Surface treatment | 2013 |
"""
    tables = emb.parse_markdown_tables(md)
    assert emb.table_to_ingredients(tables[0]) is None


def test_formulation_table_still_accepted():
    md = """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 40 |
| Zinc phosphate | 25 |
| Solvent | 35 |
"""
    tables = emb.parse_markdown_tables(md)
    row = emb.table_to_ingredients(tables[0])
    assert row is not None
    assert [i["name"] for i in row["ingredients"]] == [
        "Epoxy resin",
        "Zinc phosphate",
        "Solvent",
    ]


def test_score_bare_example_label_no_example_bonus():
    """Parser default label ``Example`` must not earn the +3 embodiment signal."""
    table = {"headers": ["Component", "wt%"], "label": "Example"}
    emb_row = {
        "label": "Example",
        "ingredients": [{"name": "A"}, {"name": "B"}],
    }
    bare = emb.score_embodiment_table(table, emb_row)
    numbered = emb.score_embodiment_table(
        {"headers": ["Component", "wt%"], "label": "Example 1"},
        {"label": "Example 1", "ingredients": emb_row["ingredients"]},
    )
    assert bare == pytest.approx(4.2)  # name_ok + amt_ok + 2 rows, no +3
    assert numbered == pytest.approx(7.2)  # +3 for real Example 1 heading
    assert numbered - bare >= 3.0


def test_parse_markdown_tables_does_not_autonumber_unlabeled():
    """After a real Example heading table, later tables keep bare Example (no Example N)."""
    md = """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 40 |
| Zinc phosphate | 25 |
| Solvent | 35 |

| Material | wt% |
| --- | --- |
| Polymer A | 50 |
| Polymer B | 50 |
"""
    tables = emb.parse_markdown_tables(md)
    assert len(tables) == 2
    assert emb._is_real_example_label(tables[0]["label"])
    assert tables[1]["label"] == "Example"
    assert not emb._is_real_example_label(tables[1]["label"])


def test_score_material_header_contributes_name_ok():
    table = {"headers": ["Material", "wt%"], "label": "Example"}
    emb_row = {
        "label": "Example",
        "ingredients": [{"name": "Polymer A"}, {"name": "Polymer B"}],
    }
    material_score = emb.score_embodiment_table(table, emb_row)
    decoy = {"headers": ["Polymer", "wt%"], "label": "Example"}
    decoy_score = emb.score_embodiment_table(decoy, emb_row)
    assert material_score == pytest.approx(4.2)  # Material|wt% earns name_ok + amt_ok
    assert decoy_score == pytest.approx(0.2)  # no name_ok without material/component family
    assert material_score - decoy_score >= 4.0


def test_extract_prefers_formulation_over_citation_table(stores):
    sources, chunks, _ = stores
    md = (
        "Patent body with enough characters for eligibility. " * 30
        + """
Related patents
| Material | wt% |
| --- | --- |
| Polymer A | 16 |
| Polymer B | 16 |
| Polymer C | 16 |
| Polymer D | 16 |
| Polymer E | 16 |
| Polymer F | 16 |

| Patent | Title | Year |
| --- | --- | --- |
| DE102011120870B4 | Prior coating | 2012 |
| CN102528001A | Mg alloy | 2011 |
| US8608869B2 | Surface | 2013 |
| EP1234567A1 | Film | 2010 |
| WO2010123456A1 | Bath | 2010 |

Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 55 |
| Zinc phosphate | 15 |
| Talc | 30 |
"""
    )
    sid = _seed_doc(sources, chunks, text=md)
    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["ok"] is True
    names = [i["name"] for i in out["draft"]["formulation"]["ingredients"]]
    assert "Epoxy resin" in names
    assert not any(emb._is_publication_number(n) for n in names)
    warnings = out["draft"]["formulation"]["warnings"]
    assert any("语义" in w for w in warnings)


def test_extract_prefers_example_heading_over_denser_unlabeled_material(stores):
    """Real Example 1 Component table must beat a denser Material table without Example heading."""
    sources, chunks, _ = stores
    md = (
        "Patent body with enough characters for eligibility. " * 30
        + """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 55 |
| Zinc phosphate | 15 |
| Talc | 30 |

| Material | wt% |
| --- | --- |
| Polymer A | 12.5 |
| Polymer B | 12.5 |
| Polymer C | 12.5 |
| Polymer D | 12.5 |
| Polymer E | 12.5 |
| Polymer F | 12.5 |
| Polymer G | 12.5 |
| Polymer H | 12.5 |
"""
    )
    sid = _seed_doc(sources, chunks, text=md)
    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["ok"] is True
    names = [i["name"] for i in out["draft"]["formulation"]["ingredients"]]
    assert names == ["Epoxy resin", "Zinc phosphate", "Talc"]
    assert "Polymer A" not in names


def test_dirty_name_lowers_confidence_and_warns():
    md = """
Example 2
| Component | wt% |
| --- | --- |
| Water and its preparation method | 40 |
| Epoxy resin | 60 |
"""
    tables = emb.parse_markdown_tables(md)
    row = emb.table_to_ingredients(tables[0])
    dirty = next(i for i in row["ingredients"] if "preparation" in i["name"].lower())
    assert dirty["confidence"] <= 0.45
    assert any("标题污染" in w or "名称可能" in w for w in row["warnings"])


def test_role_inferred_not_always_additive():
    md = """
| Component | wt% |
| --- | --- |
| Epoxy resin | 50 |
| Solvent naphtha | 50 |
"""
    tables = emb.parse_markdown_tables(md)
    row = emb.table_to_ingredients(tables[0])
    roles = {i["name"]: i["role"] for i in row["ingredients"]}
    assert roles["Epoxy resin"] == "resin"
    assert roles["Solvent naphtha"] == "solvent"
    assert "additive" not in roles.values() or roles.get("Epoxy resin") != "additive"


def test_origin_web_is_document_fulltext_not_literature():
    assert emb._origin_for_kind("web") == "document_fulltext"
    assert emb._origin_for_kind("local") == "document_fulltext"
    assert emb._origin_for_kind("literature") == "literature_fulltext"


def test_origin_from_patent_filename(stores):
    sources, chunks, _ = stores
    text = ("Eligible body. " * 40) + """
Example 1
| Component | wt% |
| --- | --- |
| Epoxy resin | 70 |
| Solvent | 30 |
"""
    sid = sources.create(
        filename="CN120693379A.pdf",
        title="一种水性分散体防腐保护涂层组合物",
        source_kind="web",
        full_text=text,
        content_hash=f"h-origin-{hash(text) & 0xFFFFFFFF:x}",
        extraction_status="ok",
        origin_url="https://patents.google.com/patent/CN120693379A",
    )
    with chunks._session_factory() as session:
        chunks.replace_for_source_in(session, sid, [{"text": text, "heading_path": "", "page_no": 1, "meta": {}}])
        session.commit()
    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["draft"]["origin"] == "patent_fulltext"


def test_extract_uses_flattened_rows_when_no_gfm(stores):
    sources, chunks, _ = stores
    body = (
        "Silane coating composition description with enough characters. " * 20
        + """
Example 1
Epoxy resin 55
Zinc phosphate 45
"""
    )
    sid = _seed_doc(sources, chunks, text=body)
    out = emb.extract_embodiment_draft(source_id=sid)
    assert out["ok"] is True
    assert out["draft"]["amount_source"] == "prose"
    names = [i["name"] for i in out["draft"]["formulation"]["ingredients"]]
    assert "Epoxy resin" in names
    assert "Zinc phosphate" in names

