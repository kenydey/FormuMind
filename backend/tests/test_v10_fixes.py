"""v10 回归测试：每个 P0/P1/P2 修复配专门测试。

v9 的教训：_normalised_distance 的 str 跳过逻辑除数错误、离散 snapshot 缺字段、
tx 路径跳质量门等问题本该被测试抓到。本文件每个测试直接对应 v10 报告中的一项。
"""
from __future__ import annotations

import math

import pytest


@pytest.fixture(autouse=True)
def _sanitize_no_proxy(monkeypatch):
    """沙箱 no_proxy 含裸 ::1，httpx 构造 Client 即炸（见 AGENTS.md）。"""
    import os

    for key in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(key, "")
        if raw:
            monkeypatch.setenv(key, ",".join(p for p in raw.split(",") if ":" not in p))


@pytest.fixture(autouse=True)
def _ensure_kb_enabled(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── P0-1: _normalised_distance 除数错误 ───────────────────────────────────────

def test_normalised_distance_all_str_returns_inf():
    """v9 bug：shared key 全是 str 时循环全部 continue，却除以 len(shared)，
    返回 0.0（"完全相同"）→ penalty_for 得 0 → active_learning 里 acq *= 0.0
    把候选彻底清零。现在应返回 inf（无可比因子）。"""
    from app.services.failure_memory import _normalised_distance

    d = _normalised_distance(
        {"温度": 105.0},
        {"温度": "high"},
        {"温度": 20.0},
    )
    assert d == float("inf"), f"全 str 应返回 inf，实际 {d}"


def test_normalised_distance_mixed_uses_only_numeric_keys():
    """混合值：只有有效数值 key 参与距离和分母。"""
    from app.services.failure_memory import _normalised_distance

    d = _normalised_distance(
        {"温度": 100.0, "溶剂": "A"},
        {"温度": 120.0, "溶剂": "B"},
        {"温度": 40.0, "溶剂": 1.0},
    )
    # 只有"温度"参与：|100-120|/40 = 0.5
    assert d == pytest.approx(0.5), f"实际 {d}"
    assert math.isfinite(d)


def test_normalised_distance_no_shared_still_inf():
    """无 shared key 时保持旧语义 inf。"""
    from app.services.failure_memory import _normalised_distance

    assert _normalised_distance({"a": 1.0}, {"b": 2.0}, {"a": 1.0}) == float("inf")


# ── P1-1: 离散 snapshot 缺 low/high/unit ─────────────────────────────────────

def test_discrete_snapshot_parses_as_doe_factor_and_lever():
    """v9 bug：runs 里字符串水平重建的离散 snapshot 只有
    {name, kind, levels}，DOEFactor(**item) / LeverSpec(**item) 直接
    ValidationError（low/high 必填）。现在应可解析。"""
    from app.domain.project_spec import lever_snapshot_from_plan
    from app.domain.schemas import DOEFactor, LeverSpec

    class _FakeRun:
        def __init__(self, natural):
            self.natural = natural

    class _FakePlan:
        factors = []
        domain = None
        runs = [
            _FakeRun({"溶剂": "丙酮"}),
            _FakeRun({"溶剂": "乙醇"}),
            _FakeRun({"溶剂": "丙酮"}),
        ]

    snap = lever_snapshot_from_plan(_FakePlan())
    assert len(snap) == 1
    item = snap[0]
    assert item["kind"] == "discrete"
    assert set(item["levels"]) == {"丙酮", "乙醇"}
    # 关键：必须能被两个 schema 解析
    f = DOEFactor(**item)
    assert f.kind == "discrete"
    lv = LeverSpec(**item)
    assert lv.kind == "discrete"
    assert lv.low <= lv.high


# ── P1-2: tx 路径质量门 ─────────────────────────────────────────────────────

def _tx_factory():
    from app.db.database import Base, make_engine, make_session_factory

    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_ingest_tx_gate_all_filtered_returns_failed(monkeypatch):
    """v9 bug：ingest_tx 调 prepare_chunk_rows 未传 gate_fn，质量门被完全跳过。
    gate 全过滤时应 failed=True 且无写入（与 index_source 同口径）。"""
    import app.services.kb_retrieval_gate as gate_mod
    from app.services.ingest_tx import ingest_document_tx

    # gate 在 ingest_document_tx 函数内导入 —— patch 源模块
    monkeypatch.setattr(
        gate_mod, "gate_ingest_rows", lambda rows, source_id=None: ([], "test_block")
    )
    text = "这是一段足够长的中文文本，用于通过 chunking 的最小长度过滤。" * 10
    result = ingest_document_tx(_tx_factory(), source_id="v10-gate", text=text, title="t")
    assert result.failed is True
    assert result.chunk_count == 0


def test_ingest_tx_blocked_source_no_write(monkeypatch):
    """blocked origin 在 tx 入口即拦截：failed=True 且不留 SourceDocument。"""
    from sqlalchemy import func

    import app.services.kb_retrieval_gate as gate_mod
    from app.db.models import SourceDocument
    from app.services.ingest_tx import ingest_document_tx

    monkeypatch.setattr(
        gate_mod, "ingest_block_reason_for_source", lambda sid: "blocked_domain"
    )
    factory = _tx_factory()
    text = "这是一段足够长的中文文本，用于通过 chunking 的最小长度过滤。" * 10
    result = ingest_document_tx(factory, source_id="v10-blocked", text=text, title="t")
    assert result.failed is True
    with factory() as s:
        n = s.query(func.count(SourceDocument.id)).filter(
            SourceDocument.id == "v10-blocked"
        ).scalar()
    assert n == 0, "blocked source 不应留下 SourceDocument"


# ── P1-3: skipped 不索引 placeholder evidence ────────────────────────────────

def _skipped_outcome():
    from app.domain.schemas import Evidence
    from app.services.ingestion import IngestOutcome

    return IngestOutcome(
        evidence=[
            Evidence(
                source="web",
                identifier="http://example.com/x",
                title="x",
                snippet="无法从该 URL 提取文本",
                relevance=0.5,
            )
        ],
        extraction_status="skipped",
    )


def test_skipped_url_ingest_does_not_index_evidence(monkeypatch):
    """4 处之一：/ingest/url 的 skipped outcome 不应调用 ColBERT 索引占位文本。"""
    import app.api.ingest as ingest_api

    calls = []
    monkeypatch.setattr(ingest_api, "ingest_url", lambda url: _skipped_outcome())
    monkeypatch.setattr(
        ingest_api.colbert_store, "index_evidence", lambda ev, **kw: calls.append(ev)
    )
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/ingest/url", json={"url": "http://example.com/x"})
    assert r.status_code == 200
    assert calls == [], f"skipped 时不应索引，实际调用 {len(calls)} 次"


def test_skipped_text_ingest_does_not_index_evidence(monkeypatch):
    """4 处之二：/ingest/text 的 skipped outcome 不应调用 ColBERT。"""
    import app.api.ingest as ingest_api

    calls = []
    monkeypatch.setattr(ingest_api, "ingest_text", lambda text, title: _skipped_outcome())
    monkeypatch.setattr(
        ingest_api.colbert_store, "index_evidence", lambda ev, **kw: calls.append(ev)
    )
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/ingest/text", json={"text": "x", "title": "t"})
    assert r.status_code == 200
    assert calls == [], f"skipped 时不应索引，实际调用 {len(calls)} 次"


def test_ok_ingest_still_indexes_evidence(monkeypatch):
    """对照：非 skipped 时索引行为不变。"""
    import app.api.ingest as ingest_api
    from app.services.ingestion import IngestOutcome

    calls = []
    ok = _skipped_outcome()
    ok.extraction_status = "ok"
    monkeypatch.setattr(ingest_api, "ingest_text", lambda text, title: ok)
    monkeypatch.setattr(
        ingest_api.colbert_store, "index_evidence", lambda ev, **kw: calls.append(ev)
    )
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/ingest/text", json={"text": "x", "title": "t"})
    assert r.status_code == 200
    assert len(calls) == 1


# ── P1-4: 指代正则误杀时间习语 ───────────────────────────────────────────────

def test_temporal_idioms_not_resolved_as_anaphora():
    """v9 补了对此/以此等，但自此/至此/从此/就此/鉴此/有鉴于此仍被误杀（6/6）。
    这些时间习语不应被替换。"""
    from app.services.chat_context import ChatTurn, _resolve_anaphora

    turns = [ChatTurn(role="user", content="环氧树脂的固化温度是多少")]
    for idiom in ["自此", "至此", "从此", "就此", "鉴此", "有鉴于此"]:
        q = f"{idiom}以后，工艺需要调整吗"
        out = _resolve_anaphora(q, turns, [])
        assert idiom in out, f"{idiom} 被误替换：{out}"


def test_true_pronouns_still_resolved():
    """对照：真代词（它/该/这个）仍应被消解。"""
    from app.services.chat_context import ChatTurn, _resolve_anaphora

    turns = [ChatTurn(role="user", content="环氧树脂的固化温度是多少")]
    out = _resolve_anaphora("它的价格呢", turns, [])
    assert out != "它的价格呢", f"真代词未被消解：{out}"


# ── P2-1: RRF relevance 归一化 ───────────────────────────────────────────────

def test_rrf_relevance_preserves_ranking(monkeypatch):
    """v9 bug：RRF 模式 hybrid_score ~0.01-0.03，被 _chunk_to_evidence 的
    max(0.05, …) 全部钳成 0.05，排名信息在 Evidence 层抹平。
    现在 top 的 relevance 应显著高于低位。"""
    import app.services.hybrid_search as hs_mod
    import app.services.kb_index as kb_index
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "kb_hybrid_fusion", "rrf", raising=False)

    class _Chunk:
        def __init__(self, sid, ord_):
            self.source_id = sid
            self.ord = ord_
            self.heading_path = ""
            self.text = "环氧树脂固化剂配方文本内容足够长以通过 snippet 截断 " * 5
            self.page_no = None
            self.paragraph_idx = None

    chunks = [_Chunk("s1", i) for i in range(3)]
    scored = [
        hs_mod.ScoredChunk(chunk=chunks[0], bm25_score=0.9, cosine_score=0.9, hybrid_score=0.032),
        hs_mod.ScoredChunk(chunk=chunks[1], bm25_score=0.5, cosine_score=0.5, hybrid_score=0.020),
        hs_mod.ScoredChunk(chunk=chunks[2], bm25_score=0.1, cosine_score=0.1, hybrid_score=0.010),
    ]
    monkeypatch.setattr(hs_mod, "hybrid_search_scored", lambda *a, **k: scored)

    evs = kb_index.search_chunks_hybrid("环氧树脂", k=3, source_meta={})
    assert len(evs) == 3
    rels = [e.relevance for e in evs]
    assert rels[0] == pytest.approx(1.0), f"top 应归一化为 1.0，实际 {rels}"
    assert rels[0] > rels[1] > rels[2], f"排名信息应保留，实际 {rels}"
    assert rels[2] < 0.05 or rels[2] == pytest.approx(0.3125, abs=0.01), (
        f"低位不应被钳成 0.05，实际 {rels}"
    )


# ── P2-2 / P2-3: infeasible 信号保留 ─────────────────────────────────────────

def _doe_plan_with_infeasible():
    from app.domain.schemas import DOEFactor, DOEPlan, DOERun

    factors = [DOEFactor(name="树脂", low=10.0, high=50.0, unit="wt%")]
    runs = [
        DOERun(run_id=i, coded={"树脂": 0.0}, natural={"树脂": 20.0},
               infeasible=(i == 1), infeasible_reason="KG 不相容" if i == 1 else None)
        for i in range(4)
    ]
    return DOEPlan(design="full_factorial", factors=factors, runs=runs)


def test_legacy_active_learning_preserves_infeasible(monkeypatch):
    """v9 bug：_legacy_active_learning_doe 重建 runs 时丢 infeasible。"""
    import app.services.active_learning as al_mod

    plan = _doe_plan_with_infeasible()
    monkeypatch.setattr(
        "app.pipeline.workflow.build_doe", lambda req, **kw: plan
    )
    monkeypatch.setattr(
        al_mod, "suggest_next_experiments",
        lambda plan_, existing, n_suggest=4: plan_.runs[:2],
    )
    from app.domain.schemas import Requirement

    out = al_mod._legacy_active_learning_doe(
        Requirement(product_type="x", application="y",
                    domain="anticorrosion_coating"),
        [], 2, "lhs",
    )
    by_id = {r.run_id: r for r in out.runs}
    assert by_id[1].infeasible is True
    assert by_id[1].infeasible_reason == "KG 不相容"
    assert by_id[0].infeasible is False


def test_resample_preserves_infeasible():
    """v9 bug：resample_plan_for_constraints 替换 run 时丢 infeasible。"""
    from app.services.doe_adaptive import resample_plan_for_constraints
    from app.domain.schemas import Requirement

    plan = _doe_plan_with_infeasible()
    # run 0 被 AI 选中且有约束警告 → 会被替换为首个干净 alternate（run 1，
    # 它携带 infeasible=True/"KG 不相容"）→ 替换后信号应保留。
    for r in plan.runs:
        r.ai_suggested = (r.run_id == 0)

    req = Requirement(product_type="x", application="y",
                      domain="anticorrosion_coating")

    import app.services.doe_adaptive as da_mod
    orig = da_mod._constraint_warnings_for_runs
    orig2 = da_mod._run_has_constraint_warnings
    # 构造：只有 run 0 有警告，其余为干净 alternate
    da_mod._constraint_warnings_for_runs = lambda req_, plan_: {0: ["警告"]}
    da_mod._run_has_constraint_warnings = lambda req_, run_: False
    try:
        out = resample_plan_for_constraints(req, plan)
    finally:
        da_mod._constraint_warnings_for_runs = orig
        da_mod._run_has_constraint_warnings = orig2
    by_id = {r.run_id: r for r in out.runs}
    # run 0 被替换为 alternate run 1 的 coded/natural，infeasible 来自 run 1
    assert by_id[0].infeasible is True
    assert by_id[0].infeasible_reason == "KG 不相容"
    assert by_id[0].ai_suggested is True


# ── P2-4: fallback 取整后重排 ─────────────────────────────────────────────────

def test_round_discrete_values_then_rescore_consistency():
    """v10：取整后展示 score 与排序必须基于取整后的真配方。

    直接测 run_optimization 太重；此处测核心不变量：
    _round_discrete_values 改变 values 后，用取整后 values 算出的 score
    才是展示/排序依据（旧代码用取整前伪值命名且不重排）。
    """
    from app.pipeline.workflow import _round_discrete_values
    from app.domain.schemas import LeverSpec

    lever = LeverSpec(name="溶剂", low=0.0, high=1.0, unit="",
                      kind="discrete", levels=["A", "B"])
    values = {"溶剂": 0.7}  # 连续伪值
    _round_discrete_values(values, {"溶剂": lever}, base=None, process={})
    # 数值 levels 最近邻取整：0.7→1.0？不 —— levels 是字符串，无数值水平，
    # 走 baseline 回退分支（base=None, process={} → levels[0]）
    assert values["溶剂"] == "A"

    num_lever = LeverSpec(name="温度", low=90.0, high=130.0, unit="C",
                          kind="discrete", levels=[90.0, 110.0, 130.0])
    values2 = {"温度": 118.0}
    _round_discrete_values(values2, {"温度": num_lever}, base=None, process={})
    assert values2["温度"] == 110.0, f"应取最近水平 110，实际 {values2['温度']}"


def test_optimization_top_names_match_true_scores():
    """端到端（小迭代）：top_formulations 的 name 分数 == form.score，
    且按 form.score 降序排列。"""
    from app.domain.schemas import Requirement
    from app.pipeline.workflow import run_optimization

    req = Requirement(
        product_type="环氧底漆",
        application="汽车",
        domain="anticorrosion_coating",
        levers=[],
    )
    result = run_optimization(req, iterations=2)
    assert result.top_formulations, "应有 top 配方"
    scores = [f.score for f in result.top_formulations]
    assert scores == sorted(scores, reverse=True), f"未按真分数重排：{scores}"
    for f in result.top_formulations:
        assert f"score {f.score:.3f}" in f.name, (
            f"name 分数与真分数不一致：{f.name} vs {f.score}"
        )


# ── P2-5/6/7: 魔数表与 NUL 检查 ──────────────────────────────────────────────

def test_looks_like_binary_bm_magic():
    """P2-5：旧代码 body[:4] in _BIN_MAGIC 中 b"BM" 永不可能命中（死代码）。
    startswith 修后 BMP 应被识别为二进制。"""
    from app.services.ingestion import _looks_like_binary

    assert _looks_like_binary(b"BM" + b"\x00" * 100) is True


def test_looks_like_binary_new_magics():
    """P2-7：补 WebP/RIFF、gzip、7z、RAR 魔数。"""
    from app.services.ingestion import _looks_like_binary

    assert _looks_like_binary(b"RIFF" + b"\x00" * 8 + b"WEBP") is True
    assert _looks_like_binary(b"\x1f\x8b" + b"\x08" * 50) is True
    assert _looks_like_binary(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 50) is True
    assert _looks_like_binary(b"Rar!\x1a\x07" + b"\x00" * 50) is True


def test_looks_like_binary_utf16_text_not_binary():
    """P2-6：UTF-16 合法文本含大量 NUL，旧代码误杀为二进制。"""
    from app.services.ingestion import _looks_like_binary

    text = ("环氧树脂固化温度 120 度，测试文本内容。" * 20).encode("utf-16")
    assert b"\x00" in text  # 确认样本确实含 NUL
    assert _looks_like_binary(text) is False, "UTF-16 文本不应判为二进制"


def test_parse_plain_utf16_decodes_correctly():
    """P0-2：UTF-16/32 必须解码正确，不能走 latin-1 兜底成乱码。"""
    from app.services.parsing import _parse_plain

    # with BOM
    assert _parse_plain("你好配方".encode("utf-16")) == "你好配方"
    assert _parse_plain("test".encode("utf-32")) == "test"
    # without BOM, LE with NULs
    assert _parse_plain("hello".encode("utf-16-le")) == "hello"
    # without BOM, LE CJK (no NUL bytes — the tricky case)
    # v13-2: 用不含控制字节的词（旧"你好世界"含控制字节是偶然通过）
    got = _parse_plain("你好配方".encode("utf-16-le"))
    assert got == "你好配方", f"LE CJK 解码错误: {got!r}"
    assert "\x00" not in got
    # without BOM, BE CJK
    assert _parse_plain("你好配方".encode("utf-16-be")) == "你好配方"
    # regressions: existing encodings unaffected
    assert _parse_plain("hello world".encode("utf-8")) == "hello world"
    assert _parse_plain("你好".encode("gbk")) == "你好"
    assert _parse_plain("café".encode("latin-1")) == "café"


def test_parse_plain_x0c_not_reinterpreted():
    """v13-2: 含 \\x0c 的正常 UTF-8 文本不得被 reinterpret 为 UTF-16。"""
    from app.services.parsing import _parse_plain

    text = "第一章 总则\x0c第二章 配方设计\n环氧树脂 10kg"
    assert _parse_plain(text.encode("utf-8")) == text


def test_looks_like_binary_plain_text_not_binary():
    """对照：纯文本仍放行。"""
    from app.services.ingestion import _looks_like_binary

    assert _looks_like_binary("纯文本内容 ".encode("utf-8") * 50) is False


# ── P2-8: chat_rewrite_context_turns=0 ───────────────────────────────────────

def test_rewrite_context_turns_zero_takes_no_context(monkeypatch):
    """v9 bug：history[-0:] == 全量历史，turns=0 本意"不取上下文"却取了全部。
    turns=0 时 follow-up 问句不应被改写（无上下文可消解）。"""
    from app.config import get_settings
    from app.services.chat_context import ChatTurn, rewrite_query

    settings = get_settings()
    monkeypatch.setattr(settings, "chat_rewrite_context_turns", 0)
    monkeypatch.setattr(settings, "chat_multi_turn_enabled", True)
    history = [ChatTurn(role="user", content="环氧树脂的固化温度是多少")]
    q, rewritten = rewrite_query("它的价格呢", history, settings=settings)
    assert q == "它的价格呢"
    assert rewritten is None, f"turns=0 时不应改写，实际 {rewritten}"


# ── P2-9: _source_meta 复用 ──────────────────────────────────────────────────

def test_search_chunks_hybrid_accepts_source_meta(monkeypatch):
    """v10：search_chunks_hybrid 接受调用方传入的 source_meta（单次检索
    只做一次全表扫描；retrieve_evidence 已 hoist）。"""
    import app.services.hybrid_search as hs_mod
    import app.services.kb_index as kb_index

    class _Chunk:
        def __init__(self, sid, ord_):
            self.source_id = sid
            self.ord = ord_
            self.heading_path = ""
            self.text = "环氧树脂固化剂配方文本内容 " * 10
            self.page_no = None
            self.paragraph_idx = None

    chunk = _Chunk("s9", 0)
    scored = [hs_mod.ScoredChunk(chunk=chunk, bm25_score=0.8,
                                 cosine_score=0.8, hybrid_score=0.8)]
    monkeypatch.setattr(hs_mod, "hybrid_search_scored", lambda *a, **k: scored)

    meta = {"s9": {"title": "自定义标题", "source_kind": "kb"}}
    evs = kb_index.search_chunks_hybrid("环氧树脂", k=1, source_meta=meta)
    assert len(evs) == 1
    assert "自定义标题" in evs[0].title


# ── P2-10: stitch 后按 relevance 重排 ─────────────────────────────────────────

def test_stitch_patent_siblings_resorts_by_relevance(monkeypatch):
    """v9 bug：sibling 追加到末尾，不按 relevance 重排 —— 低相关原文排在
    高相关 sibling 之前。现在应按 relevance 降序。"""
    import app.db.chunk_store as cs_mod
    import app.services.kb_index as kb_index
    from app.domain.schemas import Evidence

    class _Row:
        def __init__(self, source_id, ord_, tags):
            self.source_id = source_id
            self.ord = ord_
            self.heading_path = ""
            self.text = "专利文本内容 " * 10
            self.page_no = 1
            self.paragraph_idx = 0
            self.meta = {"patent_tags": tags}

    rows = [
        _Row("p1", 0, {"claim_no": 1}),
        _Row("p1", 1, {"claim_no": 1}),  # sibling of chunk 0
    ]

    class _Store:
        def get_by_source(self, sid):
            return rows if sid == "p1" else []

    monkeypatch.setattr(cs_mod, "get_chunk_store", lambda: _Store())

    evs = [
        Evidence(source="kb", identifier="kb:p1#c0", title="t0",
                 snippet="s", relevance=0.9),
        Evidence(source="kb", identifier="kb:other#c0", title="t1",
                 snippet="s", relevance=0.1),
    ]
    out = kb_index._stitch_patent_siblings(
        evs, max_siblings=2, meta={"p1": {"title": "专利", "source_kind": "kb"}}
    )
    assert len(out) == 3
    rels = [e.relevance for e in out]
    assert rels == sorted(rels, reverse=True), f"未按 relevance 重排：{rels}"
    # sibling relevance = 0.9*0.9 = 0.81，应排在 0.1 的原文之前
    assert out[1].identifier == "kb:p1#c1"
