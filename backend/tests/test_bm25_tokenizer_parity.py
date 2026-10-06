"""P1-1: BM25 tokenizer parity across retrieval paths.

The persistent-KB path (hybrid_search), the chat session RAG path (rag),
and the legacy path (kb_index) must tokenize identically — otherwise a
Chinese query that works on one path silently fails on another.
"""
from app.services.text_tokenize import tokenize


def test_tokenizer_parity_across_paths():
    from app.services import hybrid_search, kb_index
    from app.services import rag as rag_mod

    queries = [
        "附着力测试 ISO 4624 怎么做",
        "EEW 190 AHEW 95 每100份树脂需要多少份固化剂",
        "拉开法测附着力",
        "salt spray 1000h 中性盐雾",
        "",
    ]
    for q in queries:
        unified = tokenize(q)
        assert hybrid_search._tokenize(q) == unified, f"hybrid_search 偏离: {q}"
        assert rag_mod._bm25_tokenize(q) == unified, f"rag 偏离: {q}"
        assert rag_mod._tokenize(q) == unified, f"rag._tokenize 偏离: {q}"
        assert kb_index._tokens(q) == set(unified), f"kb_index 偏离: {q}"


def test_chinese_query_produces_cjk_tokens():
    # 回归：旧 rag._tokenize 是 ASCII-only，中文查询分词为空/错
    toks = tokenize("附着力测试怎么做")
    assert any("\u4e00" <= t <= "\u9fff" for t in "".join(toks)), toks
    assert "附着" in toks  # 二字重叠对


def test_ascii_path_unchanged():
    assert tokenize("ISO 4624") == ["iso", "4624"]
    assert tokenize("") == []
