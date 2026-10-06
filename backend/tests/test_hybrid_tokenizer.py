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

pytest.importorskip("jieba")


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
    assert _tokenize("EEW 190 每 100 份树脂 α-pinene 0.5%") == ["eew", "190", "每", "100", "份", "树", "脂", "α", "pinene", "0.5"]
