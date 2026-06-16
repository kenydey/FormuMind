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
