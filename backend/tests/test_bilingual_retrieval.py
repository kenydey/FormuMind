"""双语分流(D2/D3)单元测试: lang_router / query_translate / kb_bilingual。

不加载嵌入模型、不触网——kb_index.search_chunks 与 LLM 全部 mock,
只验证路由决策与降级链。
"""
from __future__ import annotations

import pytest

from app.services import kb_bilingual
from app.services.lang_router import detect_lang, model_for_lang, target_langs


class _FakeSettings:
    kb_bilingual = True
    kb_query_translate = True


# ── lang_router ─────────────────────────────────────────────────────────────


def test_detect_lang_zh_en_none():
    assert detect_lang("如何提高镁合金的耐盐雾性能") == "zh"
    assert detect_lang("salt spray corrosion resistance of magnesium alloy") == "en"
    assert detect_lang("中性盐雾 salt spray 等级") == "zh"  # 中英混合
    assert detect_lang("") is None
    assert detect_lang("!!!") is None
    assert detect_lang("abc") == "en"


def test_target_langs_routing():
    assert target_langs("如何提高耐盐雾", bilingual=True) == ["zh"]
    assert target_langs("salt spray corrosion of magnesium", bilingual=True) == ["en"]
    # 中英混合术语 → 双库同查
    assert target_langs("salt spray 耐盐雾等级", bilingual=True) == ["zh", "en"]
    assert target_langs("!!!", bilingual=True) is None
    assert target_langs("如何提高耐盐雾", bilingual=False) is None


def test_model_for_lang():
    assert "bge" in model_for_lang("zh")
    assert model_for_lang("en") == "sentence-transformers/all-MiniLM-L6-v2"
    assert model_for_lang(None) == "sentence-transformers/all-MiniLM-L6-v2"


# ── query_translate(LLM 全部 mock) ─────────────────────────────────────────


def test_translate_success(monkeypatch):
    from app.services import query_translate

    monkeypatch.setattr(
        "app.services.llm._call_with_deadline",
        lambda fn, seconds: "how to improve the salt spray corrosion resistance",
    )
    assert query_translate.translate_query_zh_to_en("如何提高耐盐雾") == (
        "how to improve the salt spray corrosion resistance"
    )


def test_translate_rejects_chinese_output(monkeypatch):
    """模型回中文 → 判为失败返回 None(防污染英文检索)。"""
    from app.services import query_translate

    monkeypatch.setattr("app.services.llm._call_with_deadline", lambda fn, s: "提高耐盐雾的方法")
    assert query_translate.translate_query_zh_to_en("如何提高耐盐雾") is None


def test_translate_none_on_exception(monkeypatch):
    from app.services import query_translate

    monkeypatch.setattr("app.services.llm._call_with_deadline", lambda fn, s: None)
    assert query_translate.translate_query_zh_to_en("如何提高耐盐雾") is None


def test_translate_empty_input():
    from app.services.query_translate import translate_query_zh_to_en

    assert translate_query_zh_to_en("") is None
    assert translate_query_zh_to_en(None) is None


# ── kb_bilingual.search(路由/合并/降级, search_chunks mock) ────────────────


class _Ev:
    def __init__(self, identifier):
        self.identifier = identifier

    @property
    def relevance(self):
        return 1.0


def _mk_search(monkeypatch, log: dict):
    """mock kb_index.search_chunks: 记录 (query, langs), 返回按 langs 区分的假结果。"""
    import app.services.kb_index as kb_index

    def fake(query, k=6, *, project_id=None, langs=None):
        log["calls"].append((query, langs))
        base = "zh" if "如何" in query else "en"
        tag = (langs or [base])[0]
        return [_Ev(f"{tag}-{query[:4]}-{i}") for i in range(2)]

    monkeypatch.setattr(kb_index, "search_chunks", fake)
    monkeypatch.setattr(
        "app.services.query_translate.translate_query_zh_to_en",
        lambda q: "translated en query",
    )


def test_search_bilingual_off_passthrough(monkeypatch):
    log: dict = {"calls": []}
    _mk_search(monkeypatch, log)

    class _S:
        kb_bilingual = False
        kb_query_translate = True

    out = kb_bilingual.search("如何提高耐盐雾", k=5, settings=_S())
    assert out and log["calls"] == [("如何提高耐盐雾", None)]


def test_search_en_query_only_en_sublibrary(monkeypatch):
    log = {"calls": []}
    _mk_search(monkeypatch, log)
    out = kb_bilingual.search("salt spray corrosion resistance", k=5, settings=_FakeSettings())
    assert log["calls"] == [("salt spray corrosion resistance", ["en"])]


def test_search_zh_query_translates_and_merges(monkeypatch):
    log = {"calls": []}
    _mk_search(monkeypatch, log)
    out = kb_bilingual.search("如何提高镁合金耐盐雾", k=8, settings=_FakeSettings())
    # zh 库 + 翻译后 en 库两次检索
    assert len(log["calls"]) == 2
    assert log["calls"][0] == ("如何提高镁合金耐盐雾", ["zh"])
    assert log["calls"][1] == ("translated en query", ["en"])
    ids = [e.identifier for e in out]
    assert ids == ids[:8] and len(set(ids)) == len(ids)  # 去重


def test_search_translate_failure_degrades_to_zh_only(monkeypatch):
    log = {"calls": []}
    _mk_search(monkeypatch, log)
    monkeypatch.setattr(
        "app.services.query_translate.translate_query_zh_to_en", lambda q: None
    )
    out = kb_bilingual.search("如何提高耐盐雾", k=8, settings=_FakeSettings())
    assert len(log["calls"]) == 1
    assert log["calls"][0][1] == ["zh"]


def test_search_translate_exception_degrades_zh_only(monkeypatch):
    """翻译抛异常 → _zh_with_translation 内部吞掉, 只走中文子库(不扩散)。"""
    log = {"calls": []}
    _mk_search(monkeypatch, log)
    monkeypatch.setattr(
        "app.services.query_translate.translate_query_zh_to_en",
        lambda q: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = kb_bilingual.search("如何提高耐盐雾", k=8, settings=_FakeSettings())
    assert len(log["calls"]) == 1
    assert log["calls"][0][1] == ["zh"]


# ── rag: bge 查询指令前缀只在 bge 模型 ─────────────────────────────────────


def test_bge_query_prefix_only_for_bge(monkeypatch):
    from app.services import rag

    assert rag.bge_query_prefix("BAAI/bge-small-zh-v1.5") != ""
    assert rag.bge_query_prefix("sentence-transformers/all-MiniLM-L6-v2") == ""
