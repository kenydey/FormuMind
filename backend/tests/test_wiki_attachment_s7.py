"""Attachment upload → dossier S7/S5 hydrate + notify hook."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.measurement_store import MeasurementStore
from app.db.models import ExperimentRow, SourceDocument
from app.db.project_store import ProjectStore
from app.db.session_utils import commit_session
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.services.wiki.dossier import (
    ensure_project_dossier,
    notify_dossier_event_for_experiment,
    patch_dossier_sections,
)
from app.services.wiki.dossier_pack import build_project_dossier_pack
from app.services.wiki.schema import project_dossier_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_store_mod
    import app.db.measurement_store as ms_mod

    db_path = tmp_path / "att_s7.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    projects = ProjectStore(factory)
    measurements = MeasurementStore(factory)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    monkeypatch.setattr(ps_mod, "_store", projects)
    monkeypatch.setattr(ms_mod, "_store", measurements)
    # measurement_store uses get_measurement_store singleton — patch factory path
    monkeypatch.setattr("app.db.database.default_session_factory", lambda: factory)
    get_settings.cache_clear()
    return {
        "wiki": wiki,
        "projects": projects,
        "factory": factory,
        "measurements": measurements,
        "root": wiki_root,
    }


def _make_project(projects: ProjectStore) -> str:
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=500,
        voc_limit_gpl=420,
    )
    return projects.create(title="附件S7项目", requirement=req).id


def _seed_experiment_with_doc(factory, *, project_id: str) -> tuple[int, str]:
    with commit_session(factory) as session:
        doc = SourceDocument(
            id="doc-qc-1",
            filename="salt-spray-cert.pdf",
            title="盐雾证书",
            source_kind="local",
            content_hash="hash-qc-1",
            project_id=project_id,
            full_text="qc",
            raw_text_chars=2,
        )
        session.add(doc)
        exp = ExperimentRow(
            domain="anticorrosion_coating",
            project_id=project_id,
            factors={"pH": 4.0},
            measured={"salt_spray_hours": 480},
            source="lab",
            label="run-1",
        )
        session.add(exp)
        session.flush()
        eid = int(exp.id)
    return eid, "doc-qc-1"


def test_pack_hydrates_attachments_into_s7_and_lab(env):
    pid = _make_project(env["projects"])
    eid, doc_id = _seed_experiment_with_doc(env["factory"], project_id=pid)
    att_id = env["measurements"].attach(eid, doc_id, kind="qc_report", note="NSS")
    assert att_id

    pack = build_project_dossier_pack(pid)
    art_rows = pack["artifacts"]["rows"]
    assert any(
        r.get("kind") == "qc_report" and "salt-spray-cert.pdf" in str(r.get("name"))
        for r in art_rows
    )
    assert any(str(r.get("uri") or "").startswith("source:") for r in art_rows)
    assert any(r.get("section") == "S7" for r in art_rows)

    lab_rows = pack["lab"]["rows"]
    assert lab_rows
    assert any("qc_report:salt-spray-cert.pdf" in str(r.get("attachment") or "") for r in lab_rows)


def test_patch_s7_renders_attachment_row(env):
    pid = _make_project(env["projects"])
    eid, doc_id = _seed_experiment_with_doc(env["factory"], project_id=pid)
    env["measurements"].attach(eid, doc_id, kind="qc_report")
    ensure_project_dossier(pid)
    out = patch_dossier_sections(pid, ["S7", "S5"])
    assert out["ok"] is True
    md = env["wiki"].read_markdown(project_dossier_path(pid)) or ""
    assert "salt-spray-cert.pdf" in md
    assert "qc_report" in md


def test_notify_for_experiment_routes_attachment_event(env, monkeypatch):
    import app.services.wiki.dossier as dossier_mod

    pid = _make_project(env["projects"])
    eid, _ = _seed_experiment_with_doc(env["factory"], project_id=pid)
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "true")
    get_settings.cache_clear()

    captured: list[list[str]] = []

    def _fake_refresh(project_id, *, campaign_id=None, sections=None, vertical=None, use_llm=False):
        assert project_id == pid
        captured.append(list(sections or []))
        return {"ok": True, "patched_sections": list(sections or [])}

    monkeypatch.setattr(dossier_mod, "refresh_dossier", _fake_refresh)
    out = notify_dossier_event_for_experiment(eid, "attachment_uploaded")
    assert out.get("ok") is True
    assert out.get("skipped") is False
    assert set(captured[0]) == {"S7_artifacts", "S5_lab_ledger", "S8_open_questions"}


def test_upload_api_notifies_attachment_uploaded(env, monkeypatch):
    pid = _make_project(env["projects"])
    eid, _ = _seed_experiment_with_doc(env["factory"], project_id=pid)
    calls: list[tuple] = []

    def fake_notify(experiment_id, event, **kwargs):
        calls.append((experiment_id, event))
        return {"ok": True, "skipped": True, "reason": "auto_patch_off", "event": event}

    monkeypatch.setattr(
        "app.services.wiki.dossier.notify_dossier_event_for_experiment",
        fake_notify,
    )
    # Bypass datalab upload — force local store path by stubbing helpers used in endpoint
    async def _no_item(**kwargs):
        return None

    async def _local_upload(content, filename, item_id=None):
        # Create a SourceDocument so attach FK succeeds
        with commit_session(env["factory"]) as session:
            session.add(
                SourceDocument(
                    id="doc-upload-api",
                    filename=filename,
                    title=filename,
                    source_kind="local",
                    content_hash="hash-up",
                    project_id=pid,
                    full_text="x",
                    raw_text_chars=1,
                )
            )
        return "doc-upload-api"

    monkeypatch.setattr("app.api.experiments._datalab_item_id_for", _no_item)
    monkeypatch.setattr("app.api.experiments._upload_or_store_locally", _local_upload)
    monkeypatch.setattr(
        "app.db.measurement_store.get_measurement_store",
        lambda: env["measurements"],
    )

    client = TestClient(app)
    r = client.post(
        f"/api/experiments/{eid}/attachments",
        files={"file": ("sem.png", io.BytesIO(b"png-bytes"), "image/png")},
        params={"kind": "microscope"},
    )
    assert r.status_code == 200, r.text
    assert calls == [(eid, "attachment_uploaded")]
