"""Regression tests for the shared LIKE escaper (dedup 2026-09-28).

``escape_like`` in ``app.db.db_common`` is the character-identical extraction
of the former per-module ``_escape_like`` in entity_store / material_store /
product_store. These tests pin the pre-merge behaviour.
"""

from app.db.db_common import escape_like


def test_escape_like_special_chars():
    assert escape_like("100%_x\\y") == "100\\%\\_x\\\\y"


def test_escape_like_plain_text_unchanged():
    assert escape_like("水性 环氧 树脂") == "水性 环氧 树脂"


def test_escape_like_empty():
    assert escape_like("") == ""


def test_stores_use_shared_escape_like():
    from app.db import entity_store, material_store, product_store

    assert entity_store.escape_like is escape_like
    assert material_store.escape_like is escape_like
    assert product_store.escape_like is escape_like
