"""Hierarchical multi-agent review layer (v0.8).

A supervisor agent (``InitializeAgent``) dispatches a formulation to expert
agents — ``ChemistAgent`` (chemical compatibility, RDKit-backed) and
``InspectorAgent`` (regulatory/REACH/VOC) — and aggregates their findings into
a single pure-JSON ``ReviewVerdict``.

Design mirrors the existing expert-agent pattern (``services/ip_analysis.py``,
``services/intent.py``): a deterministic rule core with an optional LLM polish,
fully functional offline. No agent framework is used.
"""
from __future__ import annotations

from .supervisor import InitializeAgent
from .chemist import ChemistAgent
from .inspector import InspectorAgent

__all__ = ["InitializeAgent", "ChemistAgent", "InspectorAgent"]
