"""``hybrid_search._tokenize`` - the BM25 tokenizer behind chat retrieval (round-5; found by the retrieval evaluation).

Text without Chinese was lowercased and split on blanks (``(ISO 4624 / ASTM D4541)`` -> ``(iso``, ``4624``, ``/``, ``astm``,
``d4541)``); text with Chinese went through jieba untouched, case, blanks and punctuation included. So ``ISO`` in a Chinese
question never met ``(iso`` in the English document that answers it, and every query containing a space matched every text
containing one through the blank token.
"""
from __future__ import annotations

import sys

import pytest
from app.services.hybrid_search import _tokenize
# P1-1: tokenizer moved to text_tokenize.py (unified across paths)
from app.services.text_tokenize import _tokenize_cjk

pytest.importorskip("jieba")


@pytest.fixture(autouse=True)
def _cold_token_cache():
    """The tokenizer caches by text, process-wide: a test that counts or swaps jieba must not meet another test's entries."""
    _tokenize_cjk.cache_clear()
    yield
    _tokenize_cjk.cache_clear()


def test_english_is_lowercased_and_split_on_punctuation():
    assert _tokenize("(ISO 4624 / ASTM D4541)") == ["iso", "4624", "astm", "d4541"]


def test_dotted_numbers_stay_whole():
    assert _tokenize("6.5 phr; 3.1.2 and v1.2.3, then 7.0.Done") == ["6.5", "phr", "3.1.2", "and", "v1.2.3", "then", "7.0", "done"]


def test_letters_of_other_scripts_stay_inside_their_words():
    assert _tokenize("α-pinene, 5 µm, Beschichtung für Stahl") == ["α", "pinene", "5", "µm", "beschichtung", "für", "stahl"]


def test_nothing_in_gives_nothing_out():
    assert _tokenize("") == _tokenize("   ") == _tokenize(None) == _tokenize("— / ( ) , ;") == []


def test_an_english_word_has_the_same_token_with_or_without_chinese_around_it():
    plain = _tokenize("ISO 4624 adhesion 0.5%")
    mixed = _tokenize("按 ISO 4624 测附着力 adhesion 0.5%")
    for token in ("iso", "4624", "adhesion", "0.5"):
        assert token in plain and token in mixed


def test_a_chinese_question_reaches_the_english_document_through_its_english_terms():
    question = set(_tokenize("EEW 190 AHEW 95 每 100 份树脂需要多少份固化剂"))
    document = set(_tokenize("At EEW 190 and AHEW 95, 100 parts resin take 50 parts hardener."))
    assert {"eew", "190", "ahew", "95", "100"} <= question & document


def test_a_figure_is_one_token_with_or_without_its_percent_sign_or_blank():
    assert "0.5" in _tokenize("添加量为总配方的 0.5%。") and "0.5" in _tokenize("添加量为总配方的 0.5 %")


def test_no_token_is_blank_or_punctuation_and_none_is_upper_case():
    for text in ("(ISO 4624 / ASTM D4541)", "耐盐雾 500 h (ASTM B117) — 附着力 ≥ 4B", "该助剂可改善流平性，添加量 0.5%。"):
        tokens = _tokenize(text)
        assert tokens and all(any(ch.isalnum() for ch in t) and t == t.lower() for t in tokens), tokens


def test_without_jieba_a_word_stays_a_word_and_a_chinese_character_stands_alone(monkeypatch):
    monkeypatch.setitem(sys.modules, "jieba", None)  # ``import jieba`` raises ImportError
    # the characters, then the pairs of each run of Chinese (the lone 每 has none)
    assert _tokenize("EEW 190 每 100 份树脂 α-pinene 0.5%") == [
        "eew", "190", "每", "100", "份", "树", "脂", "α", "pinene", "0.5", "份树", "树脂",
    ]


# ── Chinese is indexed twice: as the dictionary cuts it, and as overlapping pairs of characters ─────────────────────────────


def test_chinese_is_indexed_as_the_dictionary_cuts_it_and_as_overlapping_character_pairs():
    import jieba

    text = "耐盐雾性能"
    tokens = _tokenize(text)
    assert set(jieba.cut(text)) <= set(tokens)
    assert {"耐盐", "盐雾", "雾性", "性能"} <= set(tokens)


def test_a_term_the_dictionary_cuts_through_is_still_a_token():
    """jieba reads 耐盐雾性能 as 耐盐 / 雾 / 性能, so ``盐雾`` - the thing people ask for - was in no token of the text that
    answers them, and a question holding it matched nothing there (found by the retrieval evaluation)."""
    assert "盐雾" in _tokenize("涂层耐盐雾性能达到 720 h")
    assert "盐雾" in _tokenize("盐雾")


def test_a_pair_never_spans_a_blank_a_punctuation_mark_or_a_latin_word():
    for text in ("附着 力", "附着，力", "附着ISO力", "附着\n力", "附着(力)"):
        tokens = _tokenize(text)
        assert "着力" not in tokens and "附力" not in tokens, (text, tokens)
    assert "附着" in _tokenize("附着 力")


def test_a_lone_character_is_one_token_and_has_no_pair():
    assert _tokenize("锌 与 铬") == ["锌", "与", "铬"]


def test_text_without_chinese_is_not_given_pairs_of_anything():
    assert _tokenize("zinc phosphate 3.5 phr") == ["zinc", "phosphate", "3.5", "phr"]


# ── the cut is the expensive part, and the scan repeats it for every chunk on every query ─────────────────────────────────────


def test_the_dictionary_cut_runs_once_per_text(monkeypatch):
    import jieba

    calls: list[str] = []
    real_cut = jieba.cut

    def counting_cut(text, *args, **kwargs):
        calls.append(text)
        return real_cut(text, *args, **kwargs)

    monkeypatch.setattr(jieba, "cut", counting_cut)
    for _ in range(3):
        _tokenize("耐盐雾性能达到 720 h")
    assert len(calls) == 1
    _tokenize("划格法附着力")
    assert len(calls) == 2


def test_a_caller_gets_its_own_list_so_the_cache_cannot_be_edited_through_it():
    first = _tokenize("耐盐雾性能")
    first.append("junk")
    assert "junk" not in _tokenize("耐盐雾性能")


def test_with_and_without_jieba_never_share_a_cached_result(monkeypatch):
    text = "耐盐雾性能"
    with_jieba = _tokenize(text)
    monkeypatch.setitem(sys.modules, "jieba", None)
    without = _tokenize(text)
    assert without != with_jieba and {"耐", "盐", "雾", "盐雾"} <= set(without)
    monkeypatch.undo()
    assert _tokenize(text) == with_jieba


def test_the_cache_is_bounded():
    assert _tokenize_cjk.cache_info().maxsize is not None and _tokenize_cjk.cache_info().maxsize <= 20_000
