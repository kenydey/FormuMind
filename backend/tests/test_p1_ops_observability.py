"""P1 #23–26 smoke: langfuse fail-open, deepeval skip, sqlite prod warning."""
from __future__ import annotations

import logging

from app.services import llm_trace


def test_trace_generation_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: type("S", (), {"langfuse_enabled": False})(),
    )
    llm_trace._client = None
    llm_trace._client_failed = False
    with llm_trace.trace_generation("unit", input_preview="hi") as bag:
        bag["output"] = {"ok": True}
    # No exception = pass


def test_deepeval_gate_skips_by_default(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.delenv("FORMUMIND_DEEPEVAL", raising=False)
    script = Path(__file__).resolve().parents[1] / "scripts" / "deepeval_gate.py"
    spec = importlib.util.spec_from_file_location("deepeval_gate", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert mod.main() == 0


def test_golden_question_count():
    from app.resources.golden_retrieval import golden_questions, sample_documents

    assert len(golden_questions) >= 50
    assert len(sample_documents) >= 4
