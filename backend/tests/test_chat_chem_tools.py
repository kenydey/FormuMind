from __future__ import annotations

from app.config import Settings
from app.services import chat_chem_tools as cct


def test_should_enable_tools_openai_compat():
    s = Settings(chemtools_enabled=True, chat_chem_tools_enabled=True)
    assert cct.should_enable_tools("deepseek", s) is True
    assert cct.should_enable_tools("anthropic", s) is False
    s2 = Settings(chemtools_enabled=True, chat_chem_tools_enabled=False)
    assert cct.should_enable_tools("openai", s2) is False


def test_image_ref_allowlist_rejects_path():
    s = Settings()
    ctx = cct.build_tool_context(
        structure={"image_sha": "abc123"},
        attachment_source_ids=[],
        settings=s,
    )
    bad = cct.execute_tool(
        "recognize_structure",
        {"image_ref": "C:/evil/img.png"},
        ctx,
    )
    assert bad["ok"] is False
    assert "hint" in bad


def test_image_ref_allowlist_accepts_sha(monkeypatch):
    s = Settings(ocsr_enabled=True)
    ctx = cct.build_tool_context(
        structure={"image_sha": "deadbeef"},
        attachment_source_ids=[],
        settings=s,
    )

    def fake_recognize(content, **kwargs):
        return {
            "recognized": True,
            "smiles": "CCO",
            "image_sha": "deadbeef",
            "hits": [],
            "warnings": [],
        }

    monkeypatch.setattr(
        cct, "_load_image_bytes_for_ref", lambda ref, ctx: b"fake-png"
    )
    monkeypatch.setattr(
        "app.services.structure_recognize.recognize_structure_image",
        fake_recognize,
    )
    out = cct.execute_tool("recognize_structure", {"image_ref": "deadbeef"}, ctx)
    assert out["ok"] is True
    assert out.get("smiles") == "CCO"


def test_execute_mol_descriptors_degrades(monkeypatch):
    s = Settings(chemtools_enabled=True)
    ctx = cct.build_tool_context(structure=None, attachment_source_ids=[], settings=s)
    monkeypatch.setattr(
        "app.services.chemtools.mol_descriptors",
        lambda smiles: {
            "mol_wt": 46.0,
            "logp": -0.3,
            "tpsa": 20.0,
            "hbd": 1,
            "hba": 1,
            "arom_rings": 0,
        },
    )
    out = cct.execute_tool("mol_descriptors", {"smiles": "CCO"}, ctx)
    assert out["ok"] is True
    assert out["properties"]["mol_wt"] == 46.0


def test_openai_tool_schemas_omits_recognize_without_refs():
    s = Settings()
    ctx = cct.build_tool_context(structure=None, attachment_source_ids=[], settings=s)
    names = [t["function"]["name"] for t in cct.openai_tool_schemas(ctx)]
    assert "mol_descriptors" in names
    assert "recognize_structure" not in names


def test_recognize_unknown_sha_hint():
    s = Settings(ocsr_enabled=True)
    ctx = cct.build_tool_context(
        structure={"image_sha": "deadbeef"},
        attachment_source_ids=[],
        settings=s,
    )
    out = cct.execute_tool("recognize_structure", {"image_ref": "deadbeef"}, ctx)
    assert out["ok"] is False
    assert "hint" in out
