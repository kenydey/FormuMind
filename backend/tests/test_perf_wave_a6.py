"""A6 性能/修复回归测试：P-1 / P-3 / P-4 / P-5 / P-6 / P-7 / P-8 / P-9 / P-10 / B-15 / B-16。

只断言行为正确 + 缓存命中，不做 benchmark 数字断言。
"""
from __future__ import annotations

import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from app.domain.schemas import Evidence


def _ev(identifier: str, title: str = "t", **kw) -> Evidence:
    return Evidence(
        source="OpenAlex", identifier=identifier, title=title,
        snippet=kw.pop("snippet", "s"), relevance=0.5, **kw,
    )


# ── P-1: org dashboard TTL 缓存 ──────────────────────────────────────────


def test_org_dashboard_ttl_cache(monkeypatch):
    from app.api import org as org_api

    calls: list[int] = []
    monkeypatch.setattr(
        org_api, "_compute_org_dashboard",
        lambda: (calls.append(1), {"n": len(calls)})[1],
    )
    org_api.invalidate_org_dashboard_cache()
    first = org_api.org_dashboard()
    second = org_api.org_dashboard()
    assert first == second
    assert len(calls) == 1, "第二次请求应命中缓存，不重新计算"

    org_api.invalidate_org_dashboard_cache()
    org_api.org_dashboard()
    assert len(calls) == 2, "显式失效后应重新计算"

    org_api._dashboard_cache["at"] -= org_api._ORG_DASHBOARD_TTL_S + 1
    org_api.org_dashboard()
    assert len(calls) == 3, "TTL 过期后应重新计算"

    # 清理：避免进程级缓存污染同 session 的其他测试
    org_api.invalidate_org_dashboard_cache()


# ── P-3: chat skills mtime 缓存 ───────────────────────────────────────────


def _write_skill(root, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nsummary: {name} summary\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_chat_skills_mtime_cache(tmp_path, monkeypatch):
    from app.services import chat_skills as cs

    d1 = tmp_path / "bundled"
    d2 = tmp_path / "local"
    d2.mkdir(parents=True, exist_ok=True)  # 空 root 也参与扫描
    _write_skill(d1, "s1", "body-one")
    monkeypatch.setattr(cs, "_skill_roots", lambda: [d1, d2])
    cs.invalidate_chat_skills_cache()

    loads: list = []
    orig = cs._load_skill_dir

    def counting(root, *, origin):
        loads.append(str(root))
        return orig(root, origin=origin)

    monkeypatch.setattr(cs, "_load_skill_dir", counting)

    r1 = cs.list_chat_skills()
    assert [r["id"] for r in r1] == ["s1"]
    assert len(loads) == 2  # 两个 root 各扫一次

    r2 = cs.list_chat_skills()
    assert r2 == r1
    assert len(loads) == 2, "未改动时第二次调用应命中缓存"

    # include_body=False 不得破坏缓存（旧实现 pop 会污染）
    r_nb = cs.list_chat_skills(include_body=False)
    assert all("body" not in r for r in r_nb)
    r_b = cs.list_chat_skills(include_body=True)
    assert r_b[0]["body"].strip() == "body-one"
    assert len(loads) == 2

    # 新增 skill → 目录 mtime 变化 → 缓存失效
    _write_skill(d1, "s2", "body-two")
    r3 = cs.list_chat_skills()
    assert [r["id"] for r in r3] == ["s1", "s2"]
    assert len(loads) == 4, "新增 skill 后应重新扫描"

    # 原地改 SKILL.md 内容 → 文件 mtime 变化 → 缓存失效
    _write_skill(d1, "s1", "body-one-v2")
    # 同一秒内 mtime 可能不变：显式 bump
    p = d1 / "s1" / "SKILL.md"
    ns = p.stat().st_mtime_ns + 2_000_000_000
    import os

    os.utime(p, ns=(ns, ns))
    r4 = cs.get_chat_skill("s1", include_body=True)
    assert r4 is not None and r4["body"].strip() == "body-one-v2"
    assert len(loads) == 6, "SKILL.md 改动后应重新扫描"

    cs.invalidate_chat_skills_cache()


# ── P-4: OpenAlex arms 并行 + 限流 ────────────────────────────────────────


def _arm(name: str, query: str = "q", conditional: bool = False):
    from app.services.search_providers import ArmQuery

    return ArmQuery(name=name, query=query, relevance_delta=0.0,
                    conditional=conditional)


def _oa_settings(**kw):
    d = dict(openalex_multi_arm=True, openalex_arm_broad_threshold=5)
    d.update(kw)
    return SimpleNamespace(**d)


def test_openalex_arms_parallel(monkeypatch):
    """两臂同时阻塞在 barrier 上：串行实现会超时死锁。"""
    from app.services import literature as lit

    barrier = threading.Barrier(2)
    seen: list[str] = []

    def fake_search_openalex(query, limit, offset, **kw):
        seen.append(kw.get("arm"))
        barrier.wait(timeout=15)
        return [_ev(f"id-{kw.get('arm')}")]

    monkeypatch.setattr(lit, "search_openalex", fake_search_openalex)
    monkeypatch.setattr(
        "app.services.search_providers.arm_queries",
        lambda *a, **k: [_arm("precise"), _arm("recall")],
    )
    monkeypatch.setattr("app.config.get_settings", lambda: _oa_settings())
    # 限流桶容量足够大，避免测试被 pacing 拖慢
    monkeypatch.setattr(lit, "_openalex_bucket", lit._TokenBucket(1000.0))

    out = lit.openalex_arms(["x", "y"], limit=10)
    assert sorted(seen) == ["precise", "recall"]
    assert {e.identifier for e in out} == {"id-precise", "id-recall"}


def test_openalex_arms_dedupe_order_kept(monkeypatch):
    """并行后去重顺序不变：precise 行在 tie 时获胜。"""
    from app.services import literature as lit

    def fake_search_openalex(query, limit, offset, **kw):
        if kw.get("arm") == "precise":
            return [_ev("dup", title="precise version")]
        return [_ev("dup", title="recall version"), _ev("other")]

    monkeypatch.setattr(lit, "search_openalex", fake_search_openalex)
    monkeypatch.setattr(
        "app.services.search_providers.arm_queries",
        lambda *a, **k: [_arm("precise"), _arm("recall")],
    )
    monkeypatch.setattr("app.config.get_settings", lambda: _oa_settings())
    monkeypatch.setattr(lit, "_openalex_bucket", lit._TokenBucket(1000.0))

    out = lit.openalex_arms(["x"], limit=10)
    by_id = {e.identifier: e for e in out}
    assert by_id["dup"].title == "precise version"
    assert "other" in by_id


def test_token_bucket_throttles():
    from app.services import literature as lit

    b = lit._TokenBucket(rate=20.0, capacity=2.0)
    b.acquire(2)  # 容量内，立即通过
    t0 = time.monotonic()
    b.acquire(2)  # 需再攒 2 个 token ≈ 0.1s
    assert time.monotonic() - t0 >= 0.05


# ── P-5: OA enrich 并发 fetch + 串行 persist ──────────────────────────────


def test_oa_enrich_parallel_fetch_serial_persist(tmp_path, monkeypatch):
    from app.services import literature_oa_enrich as oe

    monkeypatch.setattr(oe.lm, "_data_root", lambda: tmp_path)
    man = oe.lm.empty_manifest("p1")
    man["items"] = [
        {"id": f"10.1/{i}", "doi": f"10.1/{i}", "title": f"t{i}"}
        for i in range(3)
    ]
    oe.lm.save_manifest(man)

    fetch_threads: list[int] = []
    persist_threads: list[int] = []
    main_ident = threading.get_ident()

    def fake_fetch(kind, ev, timeout, *, allow_pdf=True):
        fetch_threads.append(threading.get_ident())
        time.sleep(0.15)
        return f"TEXT for {ev.identifier}"

    def fake_persist(text, ev, kind, *, project_id=None, acquisition=None):
        persist_threads.append(threading.get_ident())
        return f"src-{ev.identifier}"

    import app.services.fulltext_fetcher as ff

    monkeypatch.setattr(ff, "_dispatch_fetch", fake_fetch)
    monkeypatch.setattr(ff, "_persist_fulltext", fake_persist)
    monkeypatch.setattr(oe, "classify", lambda ev: "literature")

    t0 = time.monotonic()
    out = oe.enrich_manifest_oa(
        "p1", settings=SimpleNamespace(
            literature_manifest_enabled=True,
            literature_oa_enrich_enabled=True,
            fulltext_timeout_s=5,
            kb_project_pdf_quota=0,
        ),
    )
    elapsed = time.monotonic() - t0

    assert out["fetched"] == 3
    assert out["persisted"] == 3
    assert len(set(fetch_threads)) > 1, "fetch 应在多个线程并发"
    assert set(persist_threads) == {main_ident}, "persist 必须串行在主线程"
    assert elapsed < 0.45, f"3×0.15s 串行应 ≥0.45s，实测 {elapsed:.2f}s 说明未并行"


# ── P-6: chunks_by_source 分页 ────────────────────────────────────────────


def _chunk_row(i: int):
    return SimpleNamespace(
        id=f"c{i}", source_id="s1", ord=i, text=f"text{i}",
        heading_path="", page_no=None, paragraph_idx=None,
        offset_start=None, offset_end=None, meta={},
    )


def test_chunks_by_source_pagination(monkeypatch):
    from app.api import kb as kb_api
    import app.db.chunk_store as chunk_store_mod

    class _Store:
        def get_by_source(self, source_id, *, limit=None, offset=0):
            assert source_id == "s1"
            rows = [_chunk_row(i) for i in range(10)]
            rows = rows[offset:]
            if limit is not None:
                rows = rows[:limit]
            return rows

    monkeypatch.setattr(chunk_store_mod, "get_chunk_store", lambda: _Store())

    default = kb_api.chunks_by_source("s1", limit=200, offset=0)
    assert len(default.chunks) == 10  # 默认 limit=200，10 条全回

    page = kb_api.chunks_by_source("s1", limit=3, offset=4)
    assert [c.ord for c in page.chunks] == [4, 5, 6]

    tail = kb_api.chunks_by_source("s1", limit=5, offset=8)
    assert [c.ord for c in tail.chunks] == [8, 9]

    # store 层收到了 SQL 分页参数（而非全量读取后 Python 切片）
    calls = []
    orig = _Store.get_by_source
    def spy(self, source_id, *, limit=None, offset=0):
        calls.append((limit, offset))
        return orig(self, source_id, limit=limit, offset=offset)
    _Store.get_by_source = spy
    kb_api.chunks_by_source("s1", limit=3, offset=4)
    assert calls == [(3, 4)]


# ── P-7: _notify 增量 + 最终只排一次 ─────────────────────────────────────


def _p7_settings():
    ns = SimpleNamespace(
        search_cache_ttl_s=0,
        search_round_deadline_s=60,
        search_mmr_enabled=False,
        search_rerank_enabled=False,
        chemtools_enabled=False,
    )
    ns.get_active_api_key = lambda: None  # QueryExpander 走 offline 路径
    return ns


def test_iter_search_single_final_rank(monkeypatch):
    from app.services import literature as lit
    from app.services.content_filter import FilterReport

    monkeypatch.setattr("app.config.get_settings", _p7_settings)

    rank_calls: list[int] = []

    def fake_rank(results, query, total_limit, **kw):
        rank_calls.append(len(results))
        return list(reversed(results)), FilterReport()

    monkeypatch.setattr(lit, "_merge_filter_rank", fake_rank)
    monkeypatch.setattr(
        "app.services.content_filter.llm_quality_judge",
        lambda final, q: (final, FilterReport()),
    )

    state = {"n": 0}

    def fetch(cursor: int):
        state["n"] += 1
        if state["n"] == 1:
            return [_ev("a"), _ev("b")]
        return []

    streams = [
        {"name": "s1", "fetch": fetch, "cursor": 0, "paged": True, "done": False},
    ]
    monkeypatch.setattr(lit, "_build_streams", lambda *a, **k: streams)

    seen: list[tuple[list, dict]] = []
    final, _ = lit.iter_search(
        "q", ["literature"], total_limit=10, per_source_cap=10,
        max_rounds=3, progress_cb=lambda partial, meta=None: seen.append((partial, meta or {})),
    )

    interim = [m for _, m in seen if not m.get("final")]
    assert interim, "应有增量进度回调"
    assert all(m.get("incremental") for m in interim)
    assert rank_calls and len(rank_calls) == 1, f"_merge_filter_rank 应只跑一次，实测 {len(rank_calls)} 次"
    # 最终结果是排过序的（mock 做了 reverse）
    assert [e.identifier for e in final] == ["b", "a"]
    # 增量 payload 是未排序的 raw 累积
    first_interim = seen[0][0]
    assert [e.identifier for e in first_interim] == ["a", "b"]


# ── P-8: experiments 搜索 SQL 过滤 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_experiments_sql_filter(tmp_path, monkeypatch):
    from app.api.experiments import search_experiments

    sm, Campaign = _p8_campaign_db(tmp_path, monkeypatch)

    with sm() as s:
        s.add(Campaign(name="c1", sample_refs=[
            _ref(1, "a", ["环氧", "固化剂"], "epoxy coating test"),
        ]))
        s.add(Campaign(name="c2", sample_refs=[
            _ref(2, "b", ["丙烯酸"], "acrylic resin"),
            _ref(3, "c", ["环氧稀释剂"], "other note"),
        ]))
        s.commit()

    # 中文 tag 命中（SQL json_extract 解码后匹配，非 raw-JSON LIKE）
    res = await search_experiments("环氧")
    assert {(r.campaign_name, r.row_id) for r in res} == {("c1", 1), ("c2", 3)}

    # 大小写不敏感
    res = await search_experiments("EPOXY")
    assert len(res) == 1 and res[0].row_id == 1

    # note 命中
    res = await search_experiments("acrylic")
    assert len(res) == 1 and res[0].row_id == 2

    # 空 q 返回全部
    res = await search_experiments("")
    assert len(res) == 3

    # 无命中
    assert await search_experiments("不存在的关键词xyz") == []


# ── P-8（第二处）: _parse_datalab_search 的 id 映射 SQL 化 ─────────────────


def _p8_campaign_db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import database as db_mod
    from app.db.models import Base, Campaign

    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(eng)
    sm = sessionmaker(bind=eng)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: sm)
    return sm, Campaign


def _ref(i, item, tags=(), note=""):
    return {"id": i, "item_id": item, "tags": list(tags), "note": note,
            "status": "Done", "planned_params": {}, "measurements": {}}


def test_parse_datalab_search_id_map_sql(tmp_path, monkeypatch):
    """id 映射只拉回 (item_id,id,campaign) 三列；映射语义与旧全表扫描一致。"""
    sm, Campaign = _p8_campaign_db(tmp_path, monkeypatch)
    with sm() as s:
        s.add(Campaign(name="c1", sample_refs=[
            _ref(1, "hit-a"),
            _ref(2, ""),            # 空 item_id 跳过
            {"id": 3},              # 缺 item_id 跳过
            {"id": "bad", "item_id": "hit-bad"},  # 非法 id → row_id 0
        ]))
        s.add(Campaign(name="c2", sample_refs=[_ref(4, "hit-b")]))
        s.commit()

    from app.api.experiments import _parse_datalab_search

    body = [
        {"item_id": "hit-a", "blocks_obj": {}, "status": "Done"},
        {"item_id": "hit-b", "blocks_obj": {}},
        {"item_id": "hit-bad", "blocks_obj": {}},
        {"item_id": "formumind_c7_r9_x", "blocks_obj": {}},  # 正则回退
        {"item_id": "unknown", "blocks_obj": {}},
    ]
    res = _parse_datalab_search(body)
    by_item = {r.item_id: r for r in res}
    assert (by_item["hit-a"].campaign_id, by_item["hit-a"].row_id,
            by_item["hit-a"].campaign_name) == (1, 1, "c1")
    assert (by_item["hit-b"].campaign_id, by_item["hit-b"].row_id,
            by_item["hit-b"].campaign_name) == (2, 4, "c2")
    assert by_item["hit-bad"].row_id == 0  # 非法 ref id 容错
    assert (by_item["formumind_c7_r9_x"].campaign_id,
            by_item["formumind_c7_r9_x"].row_id) == (7, 9)
    assert (by_item["unknown"].campaign_id, by_item["unknown"].row_id,
            by_item["unknown"].campaign_name) == (0, 0, "")


# ── P-9: MCP probe 结果缓存 ───────────────────────────────────────────────


def _mcp_server(sid="chem"):
    return {"id": sid, "command": "echo", "args": [],
            "enabled": True, "env": {}, "transport": "stdio"}


def _mcp_settings():
    return SimpleNamespace(mcp_client_enabled=True)


def test_mcp_probe_cached_by_server_and_generation(tmp_path, monkeypatch):
    from app.services import mcp_skill_docs as msd
    import app.services.mcp_client as mc

    monkeypatch.setattr(msd, "_skills_root", lambda: tmp_path / "skills")
    msd.invalidate_mcp_probe_cache()
    monkeypatch.setattr(mc, "list_mcp_servers", lambda: [_mcp_server("chem")])
    calls: list[str] = []

    def fake_probe(server, **kw):
        calls.append(server["id"])
        return {"ok": True,
                "tools": [{"name": "t", "description": "d", "inputSchema": {}}],
                "names": ["t"], "error": None}

    monkeypatch.setattr(mc, "probe_server", fake_probe)

    msd.ensure_mcp_skill_docs(server_ids=["chem"], settings=_mcp_settings(), probe=True)
    msd.ensure_mcp_skill_docs(server_ids=["chem"], settings=_mcp_settings(), probe=True)
    assert calls == ["chem"], f"第二次应命中缓存，实测 probe 调用 {calls}"

    # generation 变化 → 缓存失效，重新 probe
    mc._CONFIG_GENERATIONS["chem"] = mc._CONFIG_GENERATIONS.get("chem", 0) + 1
    try:
        msd.ensure_mcp_skill_docs(server_ids=["chem"], settings=_mcp_settings(), probe=True)
        assert calls == ["chem", "chem"]
    finally:
        mc._CONFIG_GENERATIONS.pop("chem", None)
        msd.invalidate_mcp_probe_cache()


def test_mcp_probe_failure_not_cached(tmp_path, monkeypatch):
    from app.services import mcp_skill_docs as msd
    import app.services.mcp_client as mc

    monkeypatch.setattr(msd, "_skills_root", lambda: tmp_path / "skills")
    msd.invalidate_mcp_probe_cache()
    monkeypatch.setattr(mc, "list_mcp_servers", lambda: [_mcp_server("chem2")])
    calls: list[str] = []

    def fake_probe(server, **kw):
        calls.append(server["id"])
        return {"ok": False, "tools": [], "names": [], "error": "down"}

    monkeypatch.setattr(mc, "probe_server", fake_probe)
    try:
        msd.ensure_mcp_skill_docs(server_ids=["chem2"], settings=_mcp_settings(), probe=True)
        msd.ensure_mcp_skill_docs(server_ids=["chem2"], settings=_mcp_settings(), probe=True)
        assert calls == ["chem2", "chem2"], "失败的 probe 不应缓存，每次都重试"
    finally:
        msd.invalidate_mcp_probe_cache()


# ── P-10: connectors 并行 ─────────────────────────────────────────────────


def test_gather_connector_evidence_parallel(monkeypatch):
    from app.services import connectors_builtin as cb

    barrier = threading.Barrier(2)
    order: list[str] = []

    def fake_lit(q, *, limit=8):
        order.append("lit-start")
        barrier.wait(timeout=15)
        order.append("lit-end")
        return [_ev("lit-1")]

    def fake_chem(q, *, limit=5):
        order.append("chem-start")
        barrier.wait(timeout=15)
        order.append("chem-end")
        return [_ev("chem-1")]

    monkeypatch.setattr(cb, "search_literature", fake_lit)
    monkeypatch.setattr(cb, "lookup_chemistry", fake_chem)
    monkeypatch.setattr(
        cb, "list_connectors",
        lambda *, settings=None: [{"id": "literature", "enabled": True},
                                  {"id": "chemistry", "enabled": True}],
    )

    out = cb.gather_connector_evidence("q", ["literature", "chemistry"])
    # 两个 start 都先于两个 end → 真并行（串行会是 start,end,start,end）
    assert order.index("lit-start") < order.index("chem-end")
    assert order.index("chem-start") < order.index("lit-end")
    # 输出顺序按 connector_ids 确定
    assert [e.identifier for e in out] == ["lit-1", "chem-1"]

    # 未启用的 connector 被跳过（换掉带 barrier 的 fake，避免单方死锁）
    monkeypatch.setattr(cb, "search_literature", lambda q, *, limit=8: [_ev("lit-1")])
    monkeypatch.setattr(
        cb, "list_connectors",
        lambda *, settings=None: [{"id": "literature", "enabled": True},
                                  {"id": "chemistry", "enabled": False}],
    )
    out = cb.gather_connector_evidence("q", ["literature", "chemistry"])
    assert [e.identifier for e in out] == ["lit-1"]


# ── B-15: search_round_deadline_s 声明 ────────────────────────────────────


def test_search_round_deadline_setting_declared(monkeypatch):
    from app.config import Settings

    assert Settings.model_fields["search_round_deadline_s"].default == 240
    monkeypatch.setenv("FORMUMIND_SEARCH_ROUND_DEADLINE_S", "33")
    assert Settings().search_round_deadline_s == 33.0


# ── B-16: cross-encoder 模型缓存 ──────────────────────────────────────────


def _fake_ce_module(monkeypatch, loads: list):
    fake = types.ModuleType("sentence_transformers")

    class FakeCE:
        def __init__(self, name):
            loads.append(name)
            self.name = name

        def predict(self, pairs):
            return [0.5] * len(pairs)

    fake.CrossEncoder = FakeCE
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)


def _cands():
    return [_ev(f"id{i}", title=f"doc{i}") for i in range(3)]


def test_rerank_model_cache_reuses(monkeypatch):
    from app.services import rerank_plugin
    from app.services.rerank_plugin import CrossEncoderReranker

    loads: list[str] = []
    _fake_ce_module(monkeypatch, loads)
    rerank_plugin.invalidate_rerank_model_cache()

    r1 = CrossEncoderReranker("m1")
    r1.rerank("q", _cands(), k=2)
    r2 = CrossEncoderReranker("m1")  # 全新实例
    out, applied = r2.rerank("q", _cands(), k=2)
    assert applied is True
    assert loads == ["m1"], f"同一模型应只加载一次，实测 {loads}"
    assert r1._model is r2._model, "应共享同一缓存模型对象"


def test_rerank_model_cache_eviction(monkeypatch):
    from app.services import rerank_plugin
    from app.services.rerank_plugin import CrossEncoderReranker

    loads: list[str] = []
    _fake_ce_module(monkeypatch, loads)
    rerank_plugin.invalidate_rerank_model_cache()

    for m in ("m1", "m2", "m3"):
        CrossEncoderReranker(m).rerank("q", _cands(), k=2)
    assert len(rerank_plugin._MODEL_CACHE) == 2, "最多缓存 2 个模型"
    assert "m1" not in rerank_plugin._MODEL_CACHE, "最久未用应被逐出"

    CrossEncoderReranker("m1").rerank("q", _cands(), k=2)
    assert loads.count("m1") == 2, "被逐出后再次请求应重新加载"
    rerank_plugin.invalidate_rerank_model_cache()


def test_rerank_predict_failure_evicts_model(monkeypatch):
    """predict 崩溃的模型逐出缓存：新实例重试时重新加载，而非复用坏对象。"""
    from app.services import rerank_plugin
    from app.services.rerank_plugin import CrossEncoderReranker

    loads: list[str] = []
    fake = types.ModuleType("sentence_transformers")

    class FlakyCE:
        def __init__(self, name):
            loads.append(name)

        def predict(self, pairs):
            raise RuntimeError("boom")

    fake.CrossEncoder = FlakyCE
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    rerank_plugin.invalidate_rerank_model_cache()

    out, applied = CrossEncoderReranker("m1").rerank("q", _cands(), k=2)
    assert applied is False
    assert [e.identifier for e in out] == ["id0", "id1"]  # 保持上游顺序
    assert "m1" not in rerank_plugin._MODEL_CACHE

    CrossEncoderReranker("m1").rerank("q", _cands(), k=2)
    assert loads == ["m1", "m1"], "重试应重新加载权重"
    rerank_plugin.invalidate_rerank_model_cache()
