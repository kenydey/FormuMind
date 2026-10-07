"""v9 回归测试：每个 P0/P1 修复配专门测试。

v8 的教训：签名错误 / no-op 修复 / 死代码回退链本该被测试抓到。
本文件每个测试直接对应 v9 报告中的一个 P0/P1 项。
"""
from __future__ import annotations

import json
import logging
from unittest import mock

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


# ── P0-1: 逆向设计 register_round 签名 ─────────────────────────────────────────

def _payload_with_project(pid: str = "proj-1"):
    return {
        "requirement": {
            "project_id": pid,
            "product_type": "x",
            "application": "y",
            "domain": "anticorrosion_coating",
        },
        "targets": {},
        "population": 8,
        "generations": 2,
        "seed_with_llm": False,
        "seed": 42,
    }


def test_register_inverse_design_round_signature(monkeypatch):
    """v8 的调用缺 session / recommend_id、多传 n_formulas → TypeError 被吞成 no-op。

    本测试直接断言正确签名：session 位置参数、recommend_id 字符串、
    project_id 取自 payload.requirement。
    """
    import app.worker.tasks as tasks
    import app.db.recommend_outcome_store as store

    calls = {}

    class _CM:
        def __init__(self, session):
            self._s = session

        def __enter__(self):
            return self._s

        def __exit__(self, *a):
            return False

    fake_session = object()
    monkeypatch.setattr(
        "app.db.database.default_session_factory", lambda: object()
    )
    monkeypatch.setattr(
        "app.db.session_utils.commit_session", lambda factory: _CM(fake_session)
    )

    def fake_register(session, *, recommend_id, project_id=None):
        calls["session"] = session
        calls["recommend_id"] = recommend_id
        calls["project_id"] = project_id

    monkeypatch.setattr(store, "register_round", fake_register)

    rec_id = tasks._register_inverse_design_round(_payload_with_project("proj-9"))

    assert isinstance(rec_id, str) and rec_id.startswith("inv-")
    assert calls["session"] is fake_session  # session 必须作为位置参数传入
    assert calls["recommend_id"] == rec_id  # recommend_id 关键字参数
    assert calls["project_id"] == "proj-9"  # 来自 requirement，不是 payload 顶层
    json.dumps({"recommend_id": rec_id})  # 必须是 JSON 可序列化的字符串


def test_register_inverse_design_round_fail_open_logs(monkeypatch, caplog):
    """注册失败时 fail-open 返回 None，且必须记 warning（v8 的裸 except: pass 是哑巴）。"""
    import app.worker.tasks as tasks
    import app.db.recommend_outcome_store as store

    monkeypatch.setattr(
        "app.db.database.default_session_factory", lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    )

    with caplog.at_level(logging.WARNING, logger="app.worker.tasks"):
        out = tasks._register_inverse_design_round(_payload_with_project())

    assert out is None
    assert any("inverse design round registration failed" in r.message for r in caplog.records)


# ── P0-3: tx 零 chunk 返回 IngestTxResult ──────────────────────────────────────

def test_ingest_tx_zero_chunk_returns_failed_result():
    """v8 返回裸 dict → kb.py 的 result.source_id 报 AttributeError → 500。

    现在必须返回 IngestTxResult(failed=True)，且属性访问不崩。
    """
    from sqlalchemy import func

    from app.db.database import Base, make_engine, make_session_factory
    from app.db.models import SourceDocument
    from app.services.ingest_tx import IngestTxResult, ingest_document_tx

    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    # 文本过短 → chunking 全过滤 → 0 chunk
    result = ingest_document_tx(factory, source_id="v9-zero", text="short", title="t")

    assert isinstance(result, IngestTxResult)  # 不是 dict
    assert result.source_id == "v9-zero"  # kb.py 的属性访问不抛 AttributeError
    assert result.chunk_count == 0
    assert result.failed is True

    # 零 chunk 不提交空 SourceDocument（v8 语义保留）
    with factory() as s:
        n = s.query(func.count(SourceDocument.id)).filter(
            SourceDocument.id == "v9-zero"
        ).scalar()
    assert n == 0


# ── P0-4: ParserUnavailable 必须带 hint ───────────────────────────────────────

class _FakeStreamResp:
    def __init__(self, body: bytes, content_type: str = "application/pdf"):
        self._body = body
        self.headers = {"content-type": content_type}
        self.is_redirect = False

    def raise_for_status(self):
        pass

    def iter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        pass


def _mock_download(monkeypatch, body: bytes, content_type: str):
    import httpx

    import app.services.ingestion as ing

    monkeypatch.setattr(ing, "_is_safe_url", lambda url: True)

    def fake_send(self, request, **kwargs):
        return _FakeStreamResp(body, content_type)

    monkeypatch.setattr(httpx.Client, "send", fake_send)


def test_ingest_url_pdf_no_parser_raises_with_hint(monkeypatch):
    """v7/v8 的 raise ParserUnavailable(\"pdf\") 缺 hint 参数 → TypeError。

    现在必须抛 ParserUnavailable 本体（不是 TypeError），且 hint 非空。
    """
    import app.services.parsing as parsing
    from app.services.ingestion import ingest_url
    from app.services.parsing import ParserUnavailable

    _mock_download(monkeypatch, b"%PDF-1.4 fake", "application/pdf")
    monkeypatch.setattr(parsing, "can_parse", lambda ext: False)

    with pytest.raises(ParserUnavailable) as excinfo:
        ingest_url("https://example.com/doc.pdf", persist=False)
    assert excinfo.value.hint, "hint 不能为空"
    assert excinfo.value.ext == "pdf"


def test_ingest_url_office_no_parser_raises_with_hint(monkeypatch):
    """v8 office 分支同样缺 hint → TypeError。现在补上。"""
    import app.services.parsing as parsing
    from app.services.ingestion import ingest_url
    from app.services.parsing import ParserUnavailable

    _mock_download(
        monkeypatch, b"PK\x03\x04 fake docx", "application/octet-stream"
    )
    monkeypatch.setattr(parsing, "can_parse", lambda ext: False)

    with pytest.raises(ParserUnavailable) as excinfo:
        ingest_url("https://example.com/file.docx", persist=False)
    assert excinfo.value.hint, "hint 不能为空"


# ── P0-5: 图片分支透传 persist ────────────────────────────────────────────────

def test_ingest_url_image_persist_passthrough(monkeypatch):
    """v8 图片分支漏传 persist → persist=False 时仍写 DB。"""
    import app.services.ingestion as ing
    from app.services.ingestion import IngestOutcome, ingest_url

    _mock_download(monkeypatch, b"\x89PNG fake", "image/png")

    captured = {}

    def fake_ingest_image(filename, content, *, persist=True, origin_url=None):
        captured["persist"] = persist
        captured["origin_url"] = origin_url
        return IngestOutcome(evidence=[], extraction_status="ok")

    monkeypatch.setattr(ing, "_ingest_image", fake_ingest_image)

    ingest_url("https://example.com/pic.png", persist=False)
    assert captured["persist"] is False
    assert captured["origin_url"] == "https://example.com/pic.png"


def test_ingest_url_image_failure_is_skipped(monkeypatch):
    """图片解析抛异常 → skipped + notice，不崩。"""
    import app.services.ingestion as ing
    from app.services.ingestion import ingest_url

    _mock_download(monkeypatch, b"\x89PNG fake", "image/png")

    def boom(*a, **k):
        raise RuntimeError("vision down")

    monkeypatch.setattr(ing, "_ingest_image", boom)

    out = ingest_url("https://example.com/pic.png", persist=False)
    assert out.extraction_status == "skipped"
    assert any("图片" in w for w in out.warnings)


# ── P1-1: _attach_entities 合并 meta ──────────────────────────────────────────

def test_prepare_chunk_rows_keeps_patent_tags_with_chem():
    """_attach_entities 直接赋值覆盖了 patent_tags。本测试断言两者共存。"""
    from app.services.kb_index import prepare_chunk_rows

    text = (
        "Claim 1: The coating comprises TiO2 pigment and epoxy resin binder "
        "for corrosion protection of steel substrates in marine environments. "
        "The formulation is cured at elevated temperature to form a dense film."
    )
    rows = prepare_chunk_rows(text, "v9-meta", embed=False)
    assert rows, "应产出 chunk"
    row = rows[0]
    meta = row.get("meta") or {}
    assert (meta.get("patent_tags") or {}).get("claim_no") == 1, f"patent_tags 丢失: {meta}"
    assert meta.get("chem"), f"chem 实体丢失: {meta}"


# ── P1-2: gate 在 embed/dedupe 之前 ───────────────────────────────────────────

def test_prepare_chunk_rows_gate_before_embed_dedupe(monkeypatch):
    """U-1 曾把 gate 放到 dedupe 之后 → dedupe 可能以垃圾 chunk 为保留对象
    丢掉好 chunk。现在 gate_fn 必须在 embedding/dedupe 之前执行，且被 gate
    滤掉的 chunk 不应被 embedding。"""
    import app.services.kb_dedup as dedup_mod
    import app.services.kb_index as kb_index

    events: list = []

    def fake_embed(texts, model_name=None):
        events.append(("embed", len(texts)))
        return [[0.1] * 384 for _ in texts]

    def fake_dedupe(rows, source_id, session=None, **kwargs):
        events.append(("dedupe", len(rows)))
        return rows

    def gate_fn(rows):
        events.append(("gate", len(rows)))
        return rows[:1], "test"  # 只留 1 个

    # v17 CI fix: 用 mock.patch 确保在 CI 上生效。
    with mock.patch(
        "app.services.kb_index._embed_texts", side_effect=fake_embed
    ):
        monkeypatch.setattr(dedup_mod, "dedupe_chunk_rows", fake_dedupe)
        # v16 P3-17: L1 前移 —— mock 掉 l1_dedupe_chunk_rows（直通）
        monkeypatch.setattr(
            dedup_mod, "l1_dedupe_chunk_rows", lambda rows, sid, sess=None: rows
        )

        text = " ".join(f"段落{i} " + "环氧树脂防腐涂料配方研究内容填充 " * 10 for i in range(4))
        rows = prepare_chunk_rows_gate_helper(text, gate_fn)

    kinds = [e[0] for e in events]
    assert kinds == ["gate", "embed", "dedupe"], f"顺序错误: {kinds}"
    # gate 只留 1 个 → embed 只收到 1 个文本（垃圾 chunk 不浪费 embedding）
    assert events[1] == ("embed", 1)
    assert rows is not None and len(rows) == 1


def prepare_chunk_rows_gate_helper(text, gate_fn):
    from app.services.kb_index import prepare_chunk_rows

    return prepare_chunk_rows(text, "v9-gate", embed=True, gate_fn=gate_fn)


# ── P1-3/P1-4: failure_memory str 保护 ────────────────────────────────────────

def test_failure_memory_str_factors_no_crash():
    """v7 只给 rec.factors 加了保护，candidate 和 _normalised_distance 漏了。"""
    from app.domain.schemas import ExperimentRecord, ProductDomain
    from app.services.failure_memory import _normalised_distance, factor_spans

    rec = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={"树脂": "B", "温度": 110.0},
    )
    # candidate 含 str —— 不应抛 ValueError
    spans = factor_spans([rec], {"树脂": "A", "温度": 100.0})
    assert "温度" in spans
    assert "树脂" not in spans  # str 因子跳过

    # _normalised_distance 一侧 str —— 不应崩
    d = _normalised_distance({"温度": 105.0}, {"温度": "high"}, {"温度": 20.0})
    assert d == d  # 非 NaN 即正常返回


# ── P1-5: U-3 回退链用 process 字典 ───────────────────────────────────────────

def test_round_discrete_values_uses_process_baseline():
    """v8 的 getattr(base, \"process_params\", {}) 是死代码 —— Formulation
    没有该字段。本测试断言离散工艺因子回退到 process 字典值，而非 levels[0]。"""
    from types import SimpleNamespace

    from app.pipeline.workflow import _round_discrete_values

    lever = SimpleNamespace(name="固化方式", kind="discrete", levels=["A", "B", "C"])
    base = SimpleNamespace(ingredients=[])
    process = {"固化方式": "B"}

    values = {"固化方式": "X_unknown"}
    _round_discrete_values(values, {"固化方式": lever}, base, process)
    assert values["固化方式"] == "B", "应取 process 基线，而非 levels[0]"


def test_round_discrete_values_numeric_nearest():
    """数值 levels 取最近值。"""
    from types import SimpleNamespace

    from app.pipeline.workflow import _round_discrete_values

    lever = SimpleNamespace(name="温度", kind="discrete", levels=[100, 150, 200])
    values = {"温度": 190.0}
    _round_discrete_values(values, {"温度": lever}, SimpleNamespace(ingredients=[]), {})
    assert values["温度"] == 200


# ── P1-6: 指代正则介词排除 ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "question",
    [
        "对此配方的耐盐雾性能如何？",
        "以此为基础应调整什么？",
        "为此我们调整了配方",
        "由此可见性能提升",
        "除此之外还有什么选择？",
        "与其用A不如用B？",
    ],
)
def test_anaphora_prepositions_not_replaced(question):
    """对此/以此/为此/由此/除此/与其不是代词 —— 不得被替换。"""
    from app.domain.chat_schemas import ChatTurn, ClarifiedEntity
    from app.services.chat_context import _resolve_anaphora

    turns = [ChatTurn(role="user", content="环氧树脂配方的盐雾测试")]
    clarified = [ClarifiedEntity(term="环氧树脂", resolved="环氧树脂")]
    out = _resolve_anaphora(question, turns, clarified)
    assert out == question, f"误杀: {question!r} → {out!r}"


def test_anaphora_true_pronouns_still_resolved():
    """真代词仍要被消解（回归保护，不过度排除）。"""
    from app.domain.chat_schemas import ChatTurn, ClarifiedEntity
    from app.services.chat_context import _resolve_anaphora

    turns = [ChatTurn(role="user", content="环氧树脂配方的盐雾测试")]
    clarified = [ClarifiedEntity(term="环氧树脂", resolved="环氧树脂")]
    out = _resolve_anaphora("它的耐盐雾性能如何？", turns, clarified)
    assert "环氧树脂" in out, f"真代词未消解: {out!r}"


# ── P1-7/P1-8: DOE 离散 fail-closed ───────────────────────────────────────────

def test_doe_discrete_non_full_factorial_fail_closed():
    """ccd/fractional/pb 对离散因子静默退化（星点坍缩/中间水平丢失）。
    现在必须 fail-closed。lhs 保留但记 warning。"""
    from app.domain.doe import build_plan
    from app.domain.schemas import DOEFactor

    factors = [
        DOEFactor(name="树脂", low=0, high=1, kind="discrete", levels=["A", "B", "C"]),
        DOEFactor(name="温度", low=80, high=120),
    ]
    for design in ("ccd", "fractional_factorial", "plackett_burman"):
        with pytest.raises(ValueError, match="discrete"):
            build_plan(factors, design=design)

    plan = build_plan(factors, design="lhs")
    assert "WARNING" in plan.notes


def test_pydoe_fallback_does_not_swallow_discrete_error(monkeypatch):
    """pydoe 的离散 fail-closed 曾被自身 except Exception 击穿，
    降级到 native lhs 静默 clamp。现在离散错误必须 re-raise。"""
    import app.services.engines.pydoe_engine as pe
    from app.domain.schemas import DOEFactor

    def fake_build_pydoe(*a, **k):
        raise ValueError(
            "pydoe engine does not support discrete factors ['树脂']; "
            "use engine='native' or engine='baybe'"
        )

    monkeypatch.setattr(pe, "build_pydoe_plan", fake_build_pydoe)

    factors = [DOEFactor(name="树脂", low=0, high=1, kind="discrete", levels=["A", "B"])]
    with pytest.raises(ValueError, match="discrete factors"):
        pe.build_plan_with_fallback(factors, design="lhs")


# ── P2-1: _stub_doe 离散取中间水平 ────────────────────────────────────────────

def test_stub_doe_discrete_middle_level():
    """_stub_doe 曾对离散因子算 (low+high)/2 → 无效值 0.5。现在取中间水平。"""
    from app.domain.schemas import LeverSpec, ProductDomain, Requirement
    from app.services.auto_loop import _stub_doe

    req = Requirement(
        product_type="x",
        application="y",
        domain=ProductDomain.anticorrosion_coating,
        levers=[
            LeverSpec(name="树脂", low=0, high=1, kind="discrete", levels=["A", "B", "C"])
        ],
    )
    plan = _stub_doe(req, reason="target_achieved")
    assert plan.runs, "stub 应有 runs"
    assert plan.runs[0].natural["树脂"] == "B", f"应为中间水平: {plan.runs[0].natural}"


# ── P2-2: lever_snapshot_from_plan 保留离散 ───────────────────────────────────
def test_lever_snapshot_from_plan_preserves_discrete():
    """runs 回退分支曾把 str 水平 float() 跳过 → 伪造连续范围。
    现在应重建 kind=discrete + levels。"""
    from app.domain.project_spec import lever_snapshot_from_plan
    from app.domain.schemas import DOEPlan, DOERun

    plan = DOEPlan(
        design="lhs",
        factors=[],
        runs=[
            DOERun(run_id=1, coded={}, natural={"树脂": "A"}),
            DOERun(run_id=2, coded={}, natural={"树脂": "B"}),
        ],
    )
    snap = lever_snapshot_from_plan(plan, None)
    # v10 P1-1: 快照补 low/high/unit，使其可被 DOEFactor/LeverSpec 解析。
    assert snap == [
        {
            "name": "树脂",
            "low": 0.0,
            "high": 1.0,
            "unit": "",
            "kind": "discrete",
            "levels": ["A", "B"],
        }
    ], snap


def test_doe_generate_discrete_ccd_returns_422_not_500():
    """v9: 离散因子 + ccd 经 API 应返回 422（fail-closed），而不是 500。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    body = {
        "product_type": "x",
        "application": "y",
        "domain": "anticorrosion_coating",
        "levers": [
            {
                "name": "树脂",
                "low": 0,
                "high": 1,
                "kind": "discrete",
                "levels": ["A", "B"],
            }
        ],
    }
    r = client.post("/api/doe?design=ccd&engine=native", json=body)
    assert r.status_code == 422, f"应为 422，实际 {r.status_code}: {r.text[:200]}"
    assert "discrete" in r.text
