from __future__ import annotations

from app.services.llm import _merge_tool_call_deltas, _finalize_tool_calls


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
