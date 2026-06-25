"""Offline-fallback tests for the optional engine adapters.

These verify that when Summit / Optuna / paper-qa / ChemCrow / PubChemPy /
thermo are NOT installed (the CI baseline), every adapter degrades gracefully
to the deterministic built-in behaviour and the public contracts are unchanged.
"""
from app.domain.schemas import (
    Evidence,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.services import compounds, llm, optimizer, predictor
from app.services.optimizer import BayesianOptimizer, Factor, build_optimizer
from app.services.rag import TfidfStore, build_store

_REQ = Requirement(
    domain=ProductDomain.anticorrosion_coating,
    substrate="carbon_steel",
    salt_spray_hours=500,
    film_weight_gsm=70,
    cure_temperature_c=80,
    cleaning_efficiency=90,
    voc_limit_gpl=420,
)


# ── Optimizer factory ─────────────────────────────────────────────────────────

def test_build_optimizer_falls_back_to_numpy_when_no_engine():
    factors = [Factor(name="a", low=0.0, high=10.0), Factor(name="b", low=1.0, high=5.0)]
    opt = build_optimizer(factors, seed=1)
    # In the CI baseline neither Summit nor Optuna is installed.
    if not optimizer._summit_available() and not optimizer._optuna_available():
        assert isinstance(opt, BayesianOptimizer)
        assert opt.engine == "numpy-ucb"
    # Whatever engine is chosen must honour the shared interface.
    x = opt.suggest()
    assert len(x) == 2
    opt.observe(x, 1.0)
    assert opt.best is not None
    assert opt.ranked(1)


def test_optimization_result_reports_engine():
    from app.pipeline import workflow

    res = workflow.run_optimization(_REQ, iterations=6)
    assert res.engine in {"numpy-ucb", "optuna-tpe", "summit-sobo"}
    assert len(res.history) == 6
    assert res.top_formulations


# ── RAG retrieval store ─────────────────────────────────────────────────────────

def test_build_store_returns_tfidf_fallback():
    store = build_store()
    assert isinstance(store, TfidfStore)


# ── Chat routing (paper-qa / ChemCrow absent) ──────────────────────────────────

def test_answer_question_offline_fallback_to_snippet():
    sources = [
        Evidence(
            source="local",
            identifier="d0",
            title="Note",
            snippet="Zinc phosphate passivates the steel surface and inhibits corrosion.",
            relevance=1.0,
        )
    ]
    answer, citations = llm.answer_question("防腐机理是什么？", sources, domain="anticorrosion_coating")
    assert answer
    assert citations  # re-ranked sources returned as citation chips


def test_chemistry_question_detection():
    assert llm._is_chemistry_question("What is the LogP of this inhibitor?")
    assert llm._is_chemistry_question("这个组分的溶解度如何？")
    assert not llm._is_chemistry_question("推荐一个配方")


def test_chemcrow_and_paperqa_probes_safe_without_libs():
    # Probes must never raise, regardless of whether the libs are present.
    assert isinstance(llm._chemcrow_available(), bool)
    assert isinstance(llm._paperqa_available(), bool)


# ── PubChem enrichment (pubchempy absent → no-op) ───────────────────────────────

def test_enrich_materials_noop_without_pubchempy():
    if compounds._pubchempy_available():
        return  # skip when the lib is actually installed
    sample = {"Mystery solvent": {"role": "solvent", "smiles": None, "molar_mass": None}}
    assert compounds.enrich_materials(sample) == 0
    assert sample["Mystery solvent"]["smiles"] is None


# ── thermo density (absent → nominal 1.3 kg/L) ──────────────────────────────────

def test_mixture_density_fallback():
    from app.domain.knowledge import baseline_formulation

    form = baseline_formulation(_REQ)
    rho = predictor._mixture_density_kgL(form)
    assert 0.5 < rho < 3.0  # plausible liquid density; nominal 1.3 when thermo absent


def test_predict_full_still_produces_voc():
    from app.domain.knowledge import baseline_formulation

    form = baseline_formulation(_REQ)
    props, _ = predictor.predict_full(form, {"cure_temperature_c": 80})
    assert "voc_gpl" in props
    assert "sustainability_idx" in props


# ── MoLFormer reserved hook ─────────────────────────────────────────────────────

def test_molformer_hook_is_inert():
    from app.domain.knowledge import baseline_formulation

    form = baseline_formulation(_REQ)
    assert predictor._molformer_available() is False
    assert predictor._molformer_features(form) == {}


# ── v0.4: PVC / CPVC / SBV descriptors always present for pigmented systems ───

def test_predict_full_includes_pvc_for_pigmented_formula():
    from app.domain.knowledge import baseline_formulation

    form = baseline_formulation(_REQ)
    props, _ = predictor.predict_full(form)
    assert "pvc_pct" in props, "pvc_pct must be in predicted for pigmented primer"
    assert "solids_by_volume_pct" in props


def test_pvc_descriptor_absent_for_pigment_free():
    from app.domain.knowledge import baseline_formulation
    from app.domain.schemas import Requirement, ProductDomain

    form = baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    props, _ = predictor.predict_full(form)
    assert "pvc_pct" not in props, "Pigment-free degreaser should not have pvc_pct"


# ── v0.4: color metrics (CIE76 offline fallback) ──────────────────────────────

def test_predict_full_includes_color_for_pigmented_formula():
    from app.domain.knowledge import baseline_formulation

    form = baseline_formulation(_REQ)
    props, _ = predictor.predict_full(form)
    assert "delta_e" in props, "delta_e color metric expected for pigmented primer"
    assert props["delta_e"] >= 0.0


# ── v0.4: BoTorch availability probe safe without the library ─────────────────

def test_botorch_probe_safe_without_lib():
    from app.services.optimizer import _botorch_available

    assert isinstance(_botorch_available(), bool)


def test_optimization_result_engine_includes_botorch_when_installed():
    from app.pipeline import workflow
    from app.services.optimizer import _botorch_available

    res = workflow.run_optimization(_REQ, iterations=6)
    valid = {"numpy-ucb", "optuna-tpe", "summit-sobo", "botorch-ei"}
    assert res.engine in valid, f"Unknown engine: {res.engine}"


# ── v0.4: Embedding RAG probe safe without sentence-transformers ──────────────

def test_embedding_probe_safe_without_lib():
    from app.services.rag import _embedding_available, active_rag_backend

    assert isinstance(_embedding_available(), bool)
    backend = active_rag_backend()
    assert backend in ("embedding", "tfidf")


def test_build_store_always_returns_store_with_query():
    from app.domain.schemas import Evidence
    from app.services.rag import build_store

    store = build_store()
    ev = Evidence(source="test", identifier="t1", title="Epoxy coating", snippet="test", relevance=1.0)
    store.ingest([ev])
    results = store.query("epoxy coating", k=1)
    assert len(results) >= 1


# ── v0.4: QC placeholder endpoint ─────────────────────────────────────────────

def test_qc_placeholder_returns_empty_defects():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post("/api/qc/analyze", json={"image_url": "http://example.com/img.jpg"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["defects"] == []
    assert data["engine"] == "placeholder"


# ── v0.5: Rheology (Fox Tg / Mooney) always returns VEI ──────────────────────

def test_predict_full_includes_viscoelastic_index():
    from app.domain.knowledge import baseline_formulation

    form = baseline_formulation(_REQ)
    props, _ = predictor.predict_full(form)
    assert "viscoelastic_index" in props, "viscoelastic_index should always be present"
    assert 0.0 <= props["viscoelastic_index"] <= 1.0


def test_predict_full_includes_tg_for_polymer_formula():
    from app.domain.knowledge import baseline_formulation

    form = baseline_formulation(_REQ)
    props, _ = predictor.predict_full(form)
    assert "tg_celsius" in props, "Fox Tg should be present for epoxy/polyamide formula"
    assert -100 < props["tg_celsius"] < 200


# ── v0.5: Safety checks wired into workflow ───────────────────────────────────

def test_full_safety_check_standalone():
    from app.domain.chemistry import full_safety_check
    from app.domain.knowledge import baseline_formulation
    from app.domain.schemas import ProductDomain, Requirement

    form = baseline_formulation(Requirement(domain=ProductDomain.surface_treatment))
    warnings = full_safety_check(form)
    # Surface treatment has sodium nitrite → SVHC warning expected
    assert isinstance(warnings, list)
    assert any("SVHC" in w for w in warnings), "Sodium nitrite SVHC warning expected"


# ── v0.5: Process optimizer endpoint ─────────────────────────────────────────

def test_process_optimize_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post("/api/process-optimize", json={
        "domain": "anticorrosion_coating",
        "iterations": 4,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine"] in {"numpy-ucb", "optuna-tpe", "summit-sobo", "botorch-ei"}
    assert len(data["history"]) == 4
    assert "best_params" in data
    assert "predicted_outcome" in data


# ── v0.5: IP analysis endpoint ────────────────────────────────────────────────

def test_ip_analysis_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.domain.knowledge import baseline_formulation

    client = TestClient(app)
    form = baseline_formulation(_REQ)
    payload = {
        "formulation": {
            "name": form.name,
            "domain": form.domain.value,
            "ingredients": [
                {"name": i.name, "role": i.role, "weight_pct": i.weight_pct}
                for i in form.ingredients
            ],
            "rationale": form.rationale,
            "predicted": {},
            "predicted_std": {},
            "warnings": [],
        },
        "limit_patents": 3,
    }
    resp = client.post("/api/ip/analyze", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert 0.0 <= data["novelty_score"] <= 1.0
    assert data["engine"] in ("llm", "offline-keyword")
    assert data["raw_patents_searched"] >= 1


# ── v0.5: Active Learning DOE endpoint ───────────────────────────────────────

def test_active_doe_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    payload = {
        "domain": "anticorrosion_coating",
        "substrate": "carbon_steel",
        "salt_spray_hours": 500,
        "film_weight_gsm": 70,
        "cure_temperature_c": 80,
        "cleaning_efficiency": 0,
        "voc_limit_gpl": 420,
        "ph_target": None,
        "notes": "",
        "objectives": [],
        "existing_records": [],
        "n_suggest": 3,
        "doe_design": "lhs",
    }
    resp = client.post("/api/doe/active", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    plan = body["plan"]
    assert plan["design"] == "lhs"
    ai_runs = [r for r in plan["runs"] if r.get("ai_suggested")]
    assert len(ai_runs) == 3, f"Expected 3 AI-suggested runs, got {len(ai_runs)}"
    assert body.get("engine") in ("legacy", "baybe")


# ── v0.5: MD simulation probe (LAMMPS unavailable → None) ────────────────────

def test_lammps_probe_safe_without_executable():
    from app.services.md_simulation import _lammps_available, submit_cure_simulation

    # In CI there is no LAMMPS_EXEC set → must return False / None gracefully
    if _lammps_available():
        return  # skip if somehow LAMMPS is installed
    assert _lammps_available() is False
    result = submit_cure_simulation("test-form", cure_temp_c=80.0)
    assert result is None


# ── v0.6: Intent parsing endpoint ─────────────────────────────────────────────

def test_intent_parse_endpoint_offline():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post("/api/intent/parse", json={
        "text": "开发汽车底盘环保水性环氧防腐涂料，耐盐雾1000小时，120℃固化",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["requirement"]["domain"] == "anticorrosion_coating"
    assert data["requirement"]["salt_spray_hours"] == 1000.0
    assert data["engine"] in ("llm", "offline-heuristic")
    assert "salt_spray_hours" in data["extracted_fields"]


# ── v0.6: Self-driving loop endpoint (task handle + poll) ─────────────────────

def test_loop_iterate_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    payload = {
        "domain": "anticorrosion_coating",
        "substrate": "carbon_steel",
        "salt_spray_hours": 500,
        "film_weight_gsm": 70,
        "cure_temperature_c": 80,
        "cleaning_efficiency": 0,
        "voc_limit_gpl": 420,
        "ph_target": None,
        "notes": "",
        "objectives": [],
        "optimize_iterations": 6,
        "n_suggest": 3,
    }
    resp = client.post("/api/loop/iterate", json=payload)
    assert resp.status_code == 202
    handle = resp.json()
    assert "task_id" in handle and "stream_url" in handle and "status_url" in handle

    # In the CI baseline Celery runs eager / in-thread; poll until terminal.
    import time

    for _ in range(100):
        st = client.get(handle["status_url"]).json()
        if st["state"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert st["state"] == "completed", f"loop task did not complete: {st}"
    result = st["result"]
    assert result["domain"] == "anticorrosion_coating"
    assert result["optimization"]["top_formulations"]
    ai_runs = [r for r in result["next_doe"]["runs"] if r.get("ai_suggested")]
    assert len(ai_runs) == 3


def test_complete_json_helper_safe_without_llm():
    from app.services.llm import complete_json

    # No API key in CI → _call_llm returns None → complete_json returns None
    result = complete_json('Return {"x": 1}')
    assert result is None or isinstance(result, dict)


# ── v0.7: NotebookLM retrieval source (disabled by default) ───────────────────

def test_search_notebooklm_source_offline_returns_empty():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    # notebooklm-py absent / feature disabled in CI → no results, status 200.
    resp = client.post("/api/search", json={
        "source_types": ["notebooklm"],
        "query": "epoxy resin anticorrosion",
        "limit_per_source": 3,
    })
    assert resp.status_code == 200
    assert resp.json()["evidence"] == []
