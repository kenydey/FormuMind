from __future__ import annotations

from app.config import Settings
from app.services import chat_chem_tools as cct
from app.services.llm import _finalize_tool_calls, _merge_tool_call_deltas


def test_merge_tool_call_deltas():
    acc: dict = {}
    _merge_tool_call_deltas(
        acc,
        [
            {
                "index": 0,
                "id": "c1",
                "function": {"name": "mol_descriptors", "arguments": ""},
            }
        ],
    )
    _merge_tool_call_deltas(
        acc, [{"index": 0, "function": {"arguments": '{"smiles":'}}]
    )
    _merge_tool_call_deltas(
        acc, [{"index": 0, "function": {"arguments": '"CCO"}'}}]
    )
    calls = _finalize_tool_calls(acc)
    assert calls[0]["name"] == "mol_descriptors"
    assert calls[0]["arguments"] == {"smiles": "CCO"}
    assert calls[0]["id"] == "c1"


def test_tool_loop_emits_start_result_then_tokens(monkeypatch):
    s = Settings(
        chemtools_enabled=True,
        chat_chem_tools_enabled=True,
        chat_chem_tools_max_rounds=4,
    )
    ctx = cct.build_tool_context(structure=None, attachment_source_ids=[], settings=s)
    rounds = {"n": 0}

    def fake_complete(**kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return {
                "kind": "tool_calls",
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "name": "mol_descriptors",
                        "arguments": {"smiles": "CCO"},
                        "arguments_raw": '{"smiles":"CCO"}',
                    }
                ],
            }
        return {"kind": "message", "content": "乙醇 LogP 约 -0.3。"}

    monkeypatch.setattr(cct, "complete_chat_with_tools", fake_complete)
    monkeypatch.setattr(
        cct,
        "execute_tool",
        lambda name, args, ctx: {"ok": True, "properties": {"logp": -0.3}},
    )

    events = list(
        cct.run_tool_loop_events(
            messages=[{"role": "user", "content": "乙醇 logp?"}],
            ctx=ctx,
            api_key="x",
            model="m",
            base_url=None,
            max_tokens=512,
            settings=s,
        )
    )
    types = [e["type"] for e in events]
    assert "tool_start" in types
    assert "tool_result" in types
    assert any(e["type"] == "loop_done" for e in events)
