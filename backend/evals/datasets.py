"""Loaders for the committed datasets (``evals/datasets/*.jsonl``): one JSON object per line, UTF-8."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATASETS = Path(__file__).resolve().parent / "datasets"


@dataclass(frozen=True)
class Doc:
    id: str
    lang: str
    cluster: str
    title: str
    text: str
    facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Query:
    id: str
    category: str
    lang: str
    text: str
    relevant: dict[str, int] = field(default_factory=dict)  # doc id -> graded relevance (1 related, 2 useful, 3 answers it)
    facts: tuple = ()  # strings (or lists of acceptable variants) that must reach the context for the question to be answerable

    @property
    def answerable(self) -> bool:
        return bool(self.relevant)


def _lines(name: str) -> list[dict]:
    path = DATASETS / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_corpus() -> list[Doc]:
    return [Doc(d["id"], d["lang"], d["cluster"], d["title"], d["text"], tuple(d.get("facts") or ())) for d in _lines("retrieval_corpus.jsonl")]


def load_queries() -> list[Query]:
    return [
        Query(q["id"], q["category"], q["lang"], q["text"], dict(q.get("relevant") or {}), tuple(q.get("facts") or ()))
        for q in _lines("retrieval_queries.jsonl")
    ]
