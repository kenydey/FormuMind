"""Batch E: EnvFlag.maturity field."""
from __future__ import annotations

from app.services.env_flags import FLAG_REGISTRY, list_env_flags


def test_maturity_defaults_and_experimental_marked():
    by_attr = {f.attr: f for f in FLAG_REGISTRY}
    assert getattr(by_attr["neo4j_enabled"], "maturity", "stable") == "experimental"
    assert getattr(by_attr["auto_loop_on_sync"], "maturity", "stable") == "beta"
    assert getattr(by_attr["auto_adopt_next_doe_on_loop"], "maturity", "stable") == "experimental"
    # most flags remain stable
    assert getattr(by_attr["wiki_embed_enabled"], "maturity", "stable") == "stable"


def test_list_env_flags_includes_maturity():
    rows = list_env_flags()
    assert rows
    assert all("maturity" in r for r in rows)
    neo = next(r for r in rows if r["attr"] == "neo4j_enabled")
    assert neo["maturity"] == "experimental"
