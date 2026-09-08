"""L4 LLM/rules alternatives for material substitution."""
from __future__ import annotations

from app.services.llm_alternatives import fetch_llm_alternatives


def test_rules_seed_for_hardener(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_alternatives.substitute_llm_enabled",
        lambda: False,
    )
    out = fetch_llm_alternatives(
        material="Desmodur BL 3175",
        role_hint="hardener",
        limit=5,
        allow_llm=True,
    )
    assert out["llm"]
    assert out["llm"][0]["source"] == "chemist_rules"
    assert "chemist_rules" in (out["llm_meta"].get("providers") or [])


def test_llm_suggestions_merged_without_cas(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_alternatives.substitute_llm_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.llm.complete_json",
        lambda prompt: {
            "suggestions": [
                {
                    "name": "Lanthanum nitrate",
                    "rationale": "rare-earth passivation salt",
                    "kind": "substitute_inhibitor",
                    "cas": "10099-59-9",  # must be ignored
                }
            ]
        },
    )
    out = fetch_llm_alternatives(
        material="Cerium nitrate hexahydrate",
        role_hint="inhibitor",
        limit=5,
        known_names=["Zinc phosphate"],
        allow_llm=True,
    )
    names = [r["name"] for r in out["llm"]]
    assert "Lanthanum nitrate" in names
    hit = next(r for r in out["llm"] if r["name"] == "Lanthanum nitrate")
    assert hit["cas_no"] is None
    assert hit["source"] == "llm_expand"


def test_empty_material_skipped():
    out = fetch_llm_alternatives(material="")
    assert out["llm"] == []
    assert out["llm_meta"]["skipped_reason"] == "empty_material"
