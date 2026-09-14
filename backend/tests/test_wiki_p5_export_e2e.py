"""P5.1 export formats + golden E2E dossier→report pipeline."""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import ExperimentRow
from app.db.project_store import ProjectStore
from app.db.source_store import SourceStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import (
    DOEFactor,
    DOEPlan,
    DOERun,
    Formulation,
    Ingredient,
    ProductDomain,
    Requirement,
)
from app.main import app
from app.services.wiki.dossier import ensure_project_dossier, get_dossier_pack
from app.services.wiki.report import export_report, generate_report, list_report_templates
from app.services.wiki.report_export import export_capabilities, export_bytes
from app.services.wiki.schema import project_dossier_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_LLM_NARRATIVE", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.source_store as src_mod
    import app.db.wiki_store as wiki_store_mod
    import app.db.database as db_mod

    db_path = tmp_path / "golden.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    projects = ProjectStore(factory)
    sources = SourceStore(factory)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    monkeypatch.setattr(ps_mod, "_store", projects)
    monkeypatch.setattr(src_mod, "_store", sources)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    get_settings.cache_clear()
    return {
        "wiki": wiki,
        "projects": projects,
        "sources": sources,
        "root": wiki_root,
        "factory": factory,
    }


def test_templates_include_deck():
    ids = {t["id"] for t in list_report_templates()}
    assert "deck" in ids


def test_export_capabilities_and_roundtrip_bytes():
    caps = export_capabilities()
    assert caps["md"] is True
    md = "# Hello\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n- item\n"
    raw, media, ext = export_bytes(md, "md", title="t")
    assert ext == "md" and b"Hello" in raw
    if caps.get("docx"):
        raw, media, ext = export_bytes(md, "docx", title="测试报告")
        assert ext == "docx" and raw[:2] == b"PK"
    if caps.get("pdf"):
        raw, media, ext = export_bytes(md, "pdf", title="测试报告")
        assert ext == "pdf" and raw.startswith(b"%PDF")
    if caps.get("pptx"):
        deck = "# 标题\n- a\n\n---\n\n# 第二页\n- b\n"
        raw, media, ext = export_bytes(deck, "pptx", title="Deck")
        assert ext == "pptx" and raw[:2] == b"PK"


def test_golden_req_search_doe_lab_loop_report(env):
    """金样：要求 → 文献 → DOE → 台账 → 闭环 → 卷宗 → 报告/导出."""
    projects: ProjectStore = env["projects"]
    sources: SourceStore = env["sources"]

    # 1) 技术要求
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=720,
        voc_limit_gpl=350,
        notes="金样冷轧板转化膜",
    )
    detail = projects.create(title="金样硅烷转化膜", requirement=req)
    pid = detail.id

    # 2) 检索入库（文献 source）
    text = "硅烷偶联剂水解缩合与盐雾性能综述。CAS 2530-83-8。"
    sid = sources.create(
        filename="silane-review.txt",
        title="硅烷水解综述",
        source_kind="literature",
        full_text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        project_id=pid,
        acquisition="search",
    )

    # 3) DOE + 基准配方 + 闭环历史写入 workspace
    formula = Formulation(
        name="基准浴液",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name="GPTMS", role="silane", weight_pct=2.0, cas_no="2530-83-8"),
            Ingredient(name="水", role="solvent", weight_pct=98.0),
        ],
        score=0.82,
        predicted={"salt_spray_hours": 650.0},
    )
    plan = DOEPlan(
        plan_id="doe-golden-1",
        design="lhs",
        factors=[DOEFactor(name="pH", low=3.5, high=5.5, unit="")],
        runs=[DOERun(run_id=1, coded={"pH": 0.0}, natural={"pH": 4.5})],
        notes="金样 DOE",
    )
    ws = detail.workspace.model_copy(
        update={
            "doe_plan": plan,
            "leaderboard": [formula],
            "rmse_history": [{"rmse": 0.21}, {"rmse": 0.11, "converged": False}],
        }
    )
    # also put active formulation on requirement
    req2 = req.model_copy(update={"active_formulation": formula}) if hasattr(req, "model_copy") else req
    try:
        req2 = detail.workspace.requirement.model_copy(update={"active_formulation": formula})
        ws = ws.model_copy(update={"requirement": req2})
    except Exception:
        pass
    projects.update(pid, ws.model_dump(mode="json"))

    # 4) 实验台账
    with env["factory"]() as session:
        session.add(
            ExperimentRow(
                domain="anticorrosion_coating",
                project_id=pid,
                factors={"pH": 4.5},
                measured={"salt_spray_hours": 680},
                source="lab",
                label="G-01",
            )
        )
        session.commit()

    # 5) 卷宗填实
    ensure_project_dossier(pid, vertical="silane")
    pack = get_dossier_pack(pid)
    assert pack["requirements"]["rows"], "S1 requirements"
    assert any(r.get("source_id") == sid or sid in (pack.get("literature") or {}).get("source_ids", []) for r in (pack.get("literature") or {}).get("rows") or []) or sid in pack["literature"]["source_ids"]
    assert pack["doe"]["plans"], "S4 DOE"
    assert pack["lab"]["rows"], "S5 lab"
    assert pack["loop"]["history"], "S6 loop"
    md = env["wiki"].read_markdown(project_dossier_path(pid)) or ""
    assert "720" in md
    assert "lhs" in md or "full_factorial" in md or "doe" in md.lower()

    # 6) 报告
    briefing = generate_report(pid, "briefing", persist=True)
    assert briefing["ok"] and sid in (briefing.get("source_ids") or []) or sid in briefing["markdown"]
    feas = generate_report(pid, "feasibility", persist=False)
    assert "720" in feas["markdown"] or "盐雾" in feas["markdown"] or "salt_spray" in feas["markdown"]
    deck = generate_report(pid, "deck", persist=True)
    assert "---" in deck["markdown"]
    assert "技术要求" in deck["markdown"] or "基准配方" in deck["markdown"]

    # 7) 导出
    caps = export_capabilities()
    for fmt in ("md", "docx", "pdf", "pptx"):
        if not caps.get(fmt if fmt != "pptx" else "pptx"):
            continue
        tpl = "deck" if fmt == "pptx" else "briefing"
        out = export_report(pid, tpl, fmt)
        assert out["ok"] and out["size"] > 20
        if fmt == "pdf":
            assert out["bytes"].startswith(b"%PDF")
        if fmt in ("docx", "pptx"):
            assert out["bytes"][:2] == b"PK"

    # 8) API export
    client = TestClient(app)
    r = client.post(
        "/api/wiki/dossier/report/export",
        json={"project_id": pid, "template": "briefing", "format": "pdf"},
    )
    if caps.get("pdf"):
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"%PDF")
        assert "attachment" in (r.headers.get("content-disposition") or "")
