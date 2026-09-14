"""P4.6 / trust-boundary regression: Claims Raw-only; DOE ignores dossier/report."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import Evidence, ProductDomain, Requirement
from app.services.wiki.constraints import (
    _is_doe_excluded_page,
    wiki_doe_hint_lines,
    wiki_forbidden,
    wiki_parameter_bounds,
)
from app.services.wiki.dossier import ensure_project_dossier
from app.services.wiki.report import generate_report
from app.services.wiki.retrieve import blend_wiki_evidence, filter_raw_evidence, is_wiki_evidence
from app.services.wiki.schema import dump_page, project_dossier_path, project_report_path, system_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOE_CONSTRAINTS", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_LLM_NARRATIVE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "claims_doe.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    projects = ProjectStore(factory)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    monkeypatch.setattr(ps_mod, "_store", projects)
    get_settings.cache_clear()
    return {"wiki": wiki, "projects": projects, "root": wiki_root}


def _make_project(projects: ProjectStore) -> str:
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=500,
        voc_limit_gpl=420,
    )
    return projects.create(title="Claims/DOE 回归项目", requirement=req).id


def _seed_l1_system(wiki: WikiStore) -> None:
    path = system_path("catalyst_wt")
    wiki.upsert_page(
        path=path,
        kind="system",
        title="catalyst_wt",
        norm_key="catalystwt",
        entity_id="system:catalystwt",
        markdown=dump_page(
            kind="system",
            title="catalyst_wt",
            entity_id="system:catalystwt",
            norm_key="catalystwt",
            source_ids=["s1"],
            summary="L1 catalyst window for DOE soft hints.",
            bounds=[{"name": "catalyst_wt", "min": 0.2, "max": 1.0, "unit": "wt%"}],
            forbidden=["avoid runaway exotherm above 1.5 wt%"],
        ),
        source_ids=["s1"],
        replace_source_ids=True,
    )


def _poison_front_matter(md: str, *, bound_name: str, forbidden_text: str) -> str:
    """Inject fake bounds/forbidden into existing front-matter."""
    inject = (
        f'bounds_json: [{{"name": "{bound_name}", "min": 0, "max": 99, "unit": "wt%"}}]\n'
        f'forbidden_json: ["{forbidden_text}"]\n'
    )
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) >= 3:
            return f"---{parts[1]}{inject}---{parts[2]}"
    return inject + md


def test_doe_exclude_helper():
    assert _is_doe_excluded_page(kind="theme", path="themes/system-epoxy.md")
    assert _is_doe_excluded_page(kind="theme", path="themes/project-abc.md")
    assert _is_doe_excluded_page(kind="report", path="reports/project-abc-briefing.md")
    assert _is_doe_excluded_page(kind="system", path="reports/misfiled.md")
    assert _is_doe_excluded_page(kind="material", path="themes/project-xyz.md")
    assert not _is_doe_excluded_page(kind="system", path="systems/catalyst_wt.md")
    assert not _is_doe_excluded_page(kind="pitfall", path="pitfalls/exotherm.md")


def test_poisoned_dossier_and_report_ignored_by_doe(env):
    """Even if L2/Report front-matter is poisoned, DOE must not adopt those bounds."""
    wiki: WikiStore = env["wiki"]
    _seed_l1_system(wiki)
    pid = _make_project(env["projects"])

    ensure_out = ensure_project_dossier(pid)
    d_path = ensure_out["path"]
    assert d_path == project_dossier_path(pid)
    poisoned_d = _poison_front_matter(
        wiki.read_markdown(d_path) or "",
        bound_name="dossier_poison_wt",
        forbidden_text="dossier_poison_forbidden",
    )
    wiki.upsert_page(
        path=d_path,
        kind="theme",
        title="poisoned dossier",
        norm_key=f"project-{pid}",
        entity_id=f"theme:project:{pid}",
        markdown=poisoned_d,
        source_ids=[],
        flags=["unreviewed", "llm_generated"],
        replace_source_ids=True,
    )

    report_out = generate_report(pid, "briefing", persist=True)
    r_path = report_out["path"]
    assert r_path == project_report_path(pid, "briefing")
    assert report_out["disclaimer"] == "draft_not_claims"
    poisoned_r = _poison_front_matter(
        wiki.read_markdown(r_path) or "",
        bound_name="report_poison_wt",
        forbidden_text="report_poison_forbidden",
    )
    wiki.upsert_page(
        path=r_path,
        kind="report",
        title="poisoned report",
        norm_key=f"report-{pid}-briefing",
        entity_id=f"report:project:{pid}:briefing",
        markdown=poisoned_r,
        source_ids=[],
        flags=["draft_not_claims"],
        replace_source_ids=True,
    )

    bounds = wiki_parameter_bounds()
    names = {b["name"] for b in bounds}
    paths = {b["path"] for b in bounds}
    assert "catalyst_wt" in names
    assert "dossier_poison_wt" not in names
    assert "report_poison_wt" not in names
    assert not any(p.startswith("themes/project-") for p in paths)
    assert not any(p.startswith("reports/") for p in paths)

    forbidden = wiki_forbidden()
    ftexts = {f["text"] for f in forbidden}
    fpaths = {f["path"] for f in forbidden}
    assert "avoid runaway exotherm above 1.5 wt%" in ftexts
    assert "dossier_poison_forbidden" not in ftexts
    assert "report_poison_forbidden" not in ftexts
    assert not any(p.startswith("themes/project-") for p in fpaths)
    assert not any(p.startswith("reports/") for p in fpaths)

    hints = wiki_doe_hint_lines(["catalyst_wt", "dossier_poison_wt", "report_poison_wt"])
    blob = "\n".join(hints)
    assert "catalyst_wt" in blob
    assert "themes/project-" not in blob
    assert "reports/" not in blob
    assert "dossier_poison" not in blob
    assert "report_poison" not in blob


def test_claims_filter_strips_dossier_and_report_wiki_hits(env):
    """Chat may blend dossier/report wiki snippets; Claims must drop all wiki evidence."""
    wiki: WikiStore = env["wiki"]
    pid = _make_project(env["projects"])
    ensure_project_dossier(pid)
    generate_report(pid, "briefing", persist=True)

    # Ensure searchable body tokens for keyword retrieve.
    d_path = project_dossier_path(pid)
    md = wiki.read_markdown(d_path) or ""
    wiki.upsert_page(
        path=d_path,
        kind="theme",
        title="硅烷转化膜卷宗 silane dossier epoxy",
        norm_key=f"project-{pid}",
        entity_id=f"theme:project:{pid}",
        markdown=md + "\n\n## Summary\n\nsilane dossier epoxy salt spray claims-boundary token\n",
        source_ids=["src-lit-1"],
        flags=["unreviewed"],
        replace_source_ids=True,
    )
    r_path = project_report_path(pid, "briefing")
    rmd = wiki.read_markdown(r_path) or ""
    wiki.upsert_page(
        path=r_path,
        kind="report",
        title="研发简报 epoxy silane report",
        norm_key=f"report-{pid}-briefing",
        entity_id=f"report:project:{pid}:briefing",
        markdown=rmd + "\n\n## Summary\n\nepoxy silane report draft_not_claims token\n",
        source_ids=["src-lit-1"],
        flags=["draft_not_claims"],
        replace_source_ids=True,
    )

    raw = [
        Evidence(
            source="literature",
            identifier="doi:10.1000/claims-regression",
            title="raw paper",
            snippet="raw chunk about epoxy silane salt spray",
            relevance=0.91,
        )
    ]
    merged, n = blend_wiki_evidence("epoxy silane dossier report", raw, k=8)
    assert n >= 1
    wiki_hits = [e for e in merged if is_wiki_evidence(e)]
    assert wiki_hits, "expected dossier/report or other wiki hits in chat blend"
    assert any(
        "themes/project-" in (e.identifier or "") or "reports/" in (e.identifier or "")
        for e in wiki_hits
    )

    claims = filter_raw_evidence(merged)
    assert all(not is_wiki_evidence(e) for e in claims)
    assert all(e.source != "wiki" for e in claims)
    assert any(e.identifier.startswith("doi:") for e in claims)
    assert len(claims) == len(raw)


def test_report_disclaimer_stays_draft_not_claims(env):
    pid = _make_project(env["projects"])
    out = generate_report(pid, "feasibility", persist=True)
    assert out["disclaimer"] == "draft_not_claims"
    md = out["markdown"]
    assert "Claims" in md or "draft" in md.lower() or "不得" in md
