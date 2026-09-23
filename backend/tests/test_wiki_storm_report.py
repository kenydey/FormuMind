"""STORM longform report: schema, outline/draft/orchestrator, flags, Claims/DOE isolation."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import Evidence, ProductDomain, Requirement
from app.main import app
from app.services.wiki.constraints import (
    _is_doe_excluded_page,
    wiki_forbidden,
    wiki_parameter_bounds,
)
from app.services.wiki.report import generate_report
from app.services.wiki.retrieve import blend_wiki_evidence, filter_raw_evidence, is_wiki_evidence
from app.services.wiki.schema import project_report_path
from app.services.wiki.storm_draft import draft_all_sections, plan_draft_waves
from app.services.wiki.storm_orchestrator import run_storm_report
from app.services.wiki.storm_outline import build_deterministic_outline, generate_outline
from app.services.wiki.storm_polish import stitch_and_polish
from app.services.wiki.storm_schema import (
    DEFAULT_PERSPECTIVES,
    ReportOutline,
    SectionDraft,
    SectionSpec,
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_STORM_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_LLM_NARRATIVE", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_DOE_CONSTRAINTS", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "storm.db"
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
        salt_spray_hours=720,
        voc_limit_gpl=420,
    )
    return projects.create(title="STORM 长文测试项目", requirement=req).id


def _mini_pack(project_id: str = "p1") -> dict:
    return {
        "project_id": project_id,
        "title": "硅烷转化膜",
        "domain": "anticorrosion_coating",
        "requirements": {
            "rows": [
                {
                    "metric": "salt_spray_hours",
                    "value": 720,
                    "unit": "h",
                    "direction": "maximize",
                }
            ]
        },
        "formula": {
            "rows": [
                {
                    "name": "GPTMS",
                    "role": "silane",
                    "weight_pct": 3.0,
                    "cas": "2530-83-8",
                }
            ]
        },
        "literature": {
            "rows": [
                {
                    "title": "Silane salt spray review",
                    "source_id": "src-lit-storm-1",
                    "snippet": "GPTMS improves adhesion",
                }
            ],
            "source_ids": ["src-lit-storm-1"],
        },
        "doe": {"plans": [{"design_type": "lhs", "bounds": {}, "plan_id": "doe-1"}]},
        "lab": {"rows": []},
        "loop": {"history": []},
    }


# ── Schema ──────────────────────────────────────────────────────────────


def test_section_spec_and_outline_validation():
    s = SectionSpec(
        section_id="sec_a",
        title="背景",
        retrieval_queries=["  q1  ", "", "q2"],
    )
    assert s.retrieval_queries == ["q1", "q2"]
    with pytest.raises(ValidationError):
        ReportOutline(project_id="p", topic="t", sections=[])
    outline = ReportOutline(
        project_id="p",
        topic="t",
        sections=[s],
        perspectives=list(DEFAULT_PERSPECTIVES[:2]),
    )
    assert outline.schema_version == 1
    assert len(outline.perspectives) == 2


def test_strip_unbound_citations():
    outline = build_deterministic_outline(_mini_pack(), project_id="p1", max_sections=3)
    d = SectionDraft(
        section_id=outline.sections[0].section_id,
        content_markdown="## X\n\n详见 [^1] 与幻觉 [^99]。\n",
        used_citations=["src-lit-storm-1"],
        summary="摘要一",
    )
    md, cites = stitch_and_polish(
        outline,
        {outline.sections[0].section_id: d},
        _mini_pack(),
    )
    assert "[^1]" in md
    assert "[^99]" not in md
    assert "src-lit-storm-1" in cites
    assert "draft_not_claims" in md
    assert "附录" in md
    assert "规则审查" in md
    assert "已剔除未绑定引用标记" in md or "[^99]" not in md


def test_remap_local_citations_across_sections():
    from app.services.wiki.storm_polish import remap_local_citations_to_global

    # Section A local [^1]=src-a; Section B local [^1]=src-b → global [^1]/[^2]
    body_b = "证据见 [^1] 与幻觉 [^3]。"
    remapped = remap_local_citations_to_global(
        body_b, ["src-b"], ["src-a", "src-b"]
    )
    assert "[^2]" in remapped
    assert "[^1]" not in remapped or remapped.count("[^1]") == 0
    assert "[^3]" not in remapped


def test_soft_rules_appendix_readonly(env):
    from app.services.wiki.storm_polish import collect_soft_rule_warnings

    pack = _mini_pack("p1")
    # Incompatible-ish recipe: free isocyanate + water often flagged; keep soft
    pack["formula"] = {
        "rows": [
            {"name": "HDI isocyanate", "role": "hardener", "weight_pct": 20, "cas": ""},
            {"name": "water", "role": "solvent", "weight_pct": 40, "cas": "7732-18-5"},
            {"name": "GPTMS", "role": "silane", "weight_pct": 3, "cas": "2530-83-8"},
        ]
    }
    warnings = collect_soft_rule_warnings(pack)
    assert warnings
    assert any("feasibility" in w or "acid_stability" in w for w in warnings)

    outline = build_deterministic_outline(pack, project_id="p1", max_sections=3)
    d = SectionDraft(
        section_id=outline.sections[0].section_id,
        content_markdown="## X\n\nok\n",
        used_citations=[],
        summary="s",
    )
    md, _ = stitch_and_polish(outline, {outline.sections[0].section_id: d}, pack)
    assert "附录：规则审查" in md
    assert "不**自动改配方" in md or "不自动改配方" in md
    assert "draft_not_claims" in md


# ── P5 parallel waves ───────────────────────────────────────────────────


def test_plan_draft_waves_respects_depends_on():
    sections = [
        SectionSpec(section_id="a", title="A", retrieval_queries=["q"], depends_on=[]),
        SectionSpec(section_id="b", title="B", retrieval_queries=["q"], depends_on=[]),
        SectionSpec(section_id="c", title="C", retrieval_queries=["q"], depends_on=["a"]),
        SectionSpec(section_id="d", title="D", retrieval_queries=["q"], depends_on=["a", "b"]),
    ]
    waves = plan_draft_waves(sections)
    assert len(waves) >= 2
    wave0_ids = {s.section_id for s in waves[0]}
    assert wave0_ids == {"a", "b"}
    # c and d cannot start before a (and d needs b)
    flat_after = [s.section_id for w in waves[1:] for s in w]
    assert "c" in flat_after and "d" in flat_after
    # d must not appear in a wave before both a and b are done
    done: set[str] = set()
    for w in waves:
        ids = {s.section_id for s in w}
        if "d" in ids:
            assert "a" in done and "b" in done
        done |= ids


def test_plan_draft_waves_cycle_fallback():
    sections = [
        SectionSpec(section_id="a", title="A", retrieval_queries=["q"], depends_on=["b"]),
        SectionSpec(section_id="b", title="B", retrieval_queries=["q"], depends_on=["a"]),
    ]
    waves = plan_draft_waves(sections)
    assert sum(len(w) for w in waves) == 2
    assert {s.section_id for w in waves for s in w} == {"a", "b"}


def test_parallel_draft_emits_waves_and_completes(env):
    pid = _make_project(env["projects"])
    pack = _mini_pack(pid)
    outline = build_deterministic_outline(pack, project_id=pid, max_sections=4)
    # Force two roots so wave0 has size > 1 when possible
    if len(outline.sections) >= 2:
        outline.sections[0].depends_on = []
        outline.sections[1].depends_on = []
    stages: list[str] = []
    drafts = draft_all_sections(
        outline,
        pack,
        project_id=pid,
        use_llm=False,
        parallel=True,
        max_workers=3,
        progress_cb=lambda stage, msg, prog, data=None: stages.append(stage),
    )
    assert len(drafts) == len(outline.sections)
    assert any(s.startswith("drafting_wave_") for s in stages)
    assert any(s.startswith("drafting_section_") for s in stages)
    # Dependent section still gets sliding summary from dep when declared
    for spec in outline.sections:
        if spec.depends_on:
            body = drafts[spec.section_id].content_markdown
            dep = spec.depends_on[0]
            if dep in drafts and drafts[dep].summary:
                assert "承上" in body or drafts[dep].summary[:12] in body


def test_run_storm_report_parallel_meta(env):
    pid = _make_project(env["projects"])
    out = run_storm_report(
        pid,
        topic="并行测试",
        max_sections=4,
        use_llm=False,
        parallel=True,
        max_workers=2,
        persist=False,
    )
    assert out["ok"] is True
    assert out["meta"]["parallel"] is True
    assert out["meta"]["parallel_workers"] == 2
    assert out["section_count"] >= 3


# ── Outline / draft ─────────────────────────────────────────────────────


def test_deterministic_outline_has_queries(env):
    pid = _make_project(env["projects"])
    pack = _mini_pack(pid)
    outline = build_deterministic_outline(pack, project_id=pid, max_sections=4)
    assert len(outline.sections) == 4
    assert outline.source == "deterministic"
    for s in outline.sections:
        assert s.retrieval_queries
        assert s.core_intent
    # depends_on only reference kept sections
    kept = {s.section_id for s in outline.sections}
    for s in outline.sections:
        assert set(s.depends_on) <= kept


def test_draft_sliding_summary_and_no_foreign_fulltext(env):
    pid = _make_project(env["projects"])
    pack = _mini_pack(pid)
    outline = build_deterministic_outline(pack, project_id=pid, max_sections=3)
    drafts = draft_all_sections(outline, pack, project_id=pid, use_llm=False)
    assert len(drafts) == 3
    first = outline.sections[0]
    second = outline.sections[1]
    assert drafts[first.section_id].summary
    # Second section inherits sliding summary when depends_on points at first
    body2 = drafts[second.section_id].content_markdown
    if first.section_id in second.depends_on:
        assert "承上" in body2 or drafts[first.section_id].summary[:20] in body2
    # Each draft is scoped to its own heading (no wholesale paste of another chapter body)
    for sid, d in drafts.items():
        own = next(s for s in outline.sections if s.section_id == sid)
        assert f"## {own.title}" in d.content_markdown
        # Foreign full section markdown (with their intent block as sole chapter) not duplicated
        for other in outline.sections:
            if other.section_id == sid:
                continue
            foreign_header_block = f"## {other.title}\n\n> 本章意图：{other.core_intent}"
            assert foreign_header_block not in d.content_markdown


def test_generate_outline_flag_gate(env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_STORM_REPORT_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(PermissionError, match="wiki_storm_report_enabled"):
        generate_outline(_mini_pack(), project_id="p", use_llm=False)


# ── Orchestrator ────────────────────────────────────────────────────────


def test_run_storm_report_persists(env):
    pid = _make_project(env["projects"])
    stages: list[str] = []

    def cb(stage, message, progress, data=None):
        stages.append(stage)

    out = run_storm_report(
        pid,
        topic="硅烷盐雾 720h 可行性",
        max_sections=4,
        use_llm=False,
        ensure_dossier=True,
        persist=True,
        progress_cb=cb,
    )
    assert out["ok"] is True
    assert out["disclaimer"] == "draft_not_claims"
    assert out["path"] == project_report_path(pid, "storm")
    assert out["section_count"] >= 3
    row = env["wiki"].get_by_path(out["path"])
    assert row is not None
    assert "storm" in (row.flags or [])
    assert "draft" in (row.flags or [])
    md = env["wiki"].read_markdown(out["path"]) or ""
    assert "draft_not_claims" in md
    assert "附录" in md or "确定性" in md
    # outline sidecar
    outline_rel = out.get("outline_path") or ""
    if outline_rel:
        assert (env["root"] / outline_rel).exists()
        data = json.loads((env["root"] / outline_rel).read_text(encoding="utf-8"))
        assert data.get("sections")
    assert "generating_outline" in stages
    assert any(s.startswith("drafting_section_") for s in stages)
    assert "stitching" in stages or "linting" in stages or "done" in stages


def test_sync_briefing_unaffected(env):
    """Zero regression: short template report still works with storm flag on."""
    pid = _make_project(env["projects"])
    briefing = generate_report(pid, "briefing", persist=True)
    assert briefing["ok"] is True
    assert briefing["path"] == project_report_path(pid, "briefing")
    storm = run_storm_report(pid, use_llm=False, persist=True, max_sections=3)
    assert storm["path"] != briefing["path"]
    assert env["wiki"].get_by_path(briefing["path"]) is not None
    assert env["wiki"].get_by_path(storm["path"]) is not None


def test_orchestrator_flag_off(env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_STORM_REPORT_ENABLED", "false")
    get_settings.cache_clear()
    pid = _make_project(env["projects"])
    with pytest.raises(PermissionError):
        run_storm_report(pid, use_llm=False, persist=False)


# ── API ─────────────────────────────────────────────────────────────────


def test_api_storm_flag_409(env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_STORM_REPORT_ENABLED", "false")
    get_settings.cache_clear()
    pid = _make_project(env["projects"])
    client = TestClient(app)
    r = client.post("/api/wiki/storm/report", json={"project_id": pid})
    assert r.status_code == 409
    assert "wiki_storm_report_enabled" in r.json()["detail"]


def test_api_storm_202_and_get(env):
    pid = _make_project(env["projects"])
    client = TestClient(app)
    r = client.post(
        "/api/wiki/storm/report",
        json={
            "project_id": pid,
            "topic": "STORM API 测试",
            "max_sections": 3,
            "use_llm": False,
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body.get("task_id")
    assert body.get("stream_url")
    assert body.get("disclaimer") == "draft_not_claims"

    # Eager mode runs in background thread — wait for page
    path = project_report_path(pid, "storm")
    deadline = time.time() + 30
    while time.time() < deadline:
        if env["wiki"].get_by_path(path) is not None:
            break
        time.sleep(0.2)
    assert env["wiki"].get_by_path(path) is not None

    g = client.get(f"/api/wiki/storm/report/{pid}")
    assert g.status_code == 200
    assert g.json()["disclaimer"] == "draft_not_claims"
    assert "storm" in (g.json().get("flags") or []) or "draft" in (
        g.json().get("flags") or []
    )


# ── Claims / DOE isolation ──────────────────────────────────────────────


def _poison_front_matter(md: str, *, bound_name: str, forbidden_text: str) -> str:
    inject = (
        f'bounds_json: [{{"name": "{bound_name}", "min": 0, "max": 99, "unit": "wt%"}}]\n'
        f'forbidden_json: ["{forbidden_text}"]\n'
    )
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) >= 3:
            return f"---{parts[1]}{inject}---{parts[2]}"
    return inject + md


def test_storm_excluded_from_doe_and_claims(env):
    pid = _make_project(env["projects"])
    out = run_storm_report(pid, use_llm=False, persist=True, max_sections=3)
    path = out["path"]
    assert _is_doe_excluded_page(kind="report", path=path)

    wiki: WikiStore = env["wiki"]
    poisoned = _poison_front_matter(
        wiki.read_markdown(path) or "",
        bound_name="storm_poison_wt",
        forbidden_text="storm_poison_forbidden_text",
    )
    row = wiki.get_by_path(path)
    wiki.upsert_page(
        path=path,
        kind="report",
        title=row.title if row else "storm",
        norm_key=row.norm_key if row else f"storm-{pid}",
        entity_id=row.entity_id if row else f"report:storm:{pid}",
        markdown=poisoned,
        source_ids=list(row.source_ids or []) if row else [],
        flags=list(row.flags or ["storm", "draft"]) if row else ["storm", "draft"],
        replace_source_ids=True,
    )

    bounds = wiki_parameter_bounds()
    names = {b["name"] for b in bounds}
    paths = {b["path"] for b in bounds}
    assert "storm_poison_wt" not in names
    assert not any(p.startswith("reports/") for p in paths)

    forbidden = wiki_forbidden()
    ftexts = {f["text"] for f in forbidden}
    assert "storm_poison_forbidden_text" not in ftexts

    raw = [
        Evidence(
            source="literature",
            identifier="doi:10.1000/storm-claims",
            title="raw",
            snippet="raw epoxy silane",
            relevance=0.9,
        )
    ]
    merged, _n = blend_wiki_evidence("硅烷 STORM 长文", raw, k=8)
    filtered = filter_raw_evidence(merged)
    assert all(not is_wiki_evidence(e) for e in filtered)
    assert filtered
