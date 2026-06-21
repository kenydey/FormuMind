"""Minimal agent contract shared by the expert agents.

Kept deliberately tiny — no framework, just a typing.Protocol so the supervisor
can treat every expert uniformly while each agent stays a plain Python class.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.schemas import AgentFinding, Formulation, Requirement


@runtime_checkable
class ExpertAgent(Protocol):
    """An expert agent inspects a formulation and returns a single finding."""

    name: str

    def inspect(
        self,
        form: Formulation,
        requirement: Requirement | None = None,
        explain: bool = True,
    ) -> AgentFinding:
        ...


# Status severity ordering, shared by agents and the supervisor.
STATUS_RANK = {"pass": 0, "warn": 1, "intercept": 2}


def worst_status(statuses: list[str]) -> str:
    """Return the most severe status in the list (defaults to 'pass')."""
    return max(statuses, key=lambda s: STATUS_RANK.get(s, 0)) if statuses else "pass"
