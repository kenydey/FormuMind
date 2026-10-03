"""``llm.recommend_formulations`` looks up similar past experiments for prompt
context. It loaded EVERY ExperimentRow of every domain on each call (then
``find_similar_formulations`` discarded the other domains in Python), a cost that
grew with the whole ledger. The query is now scoped to the request's domain and
to the newest rows.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.db.models import ExperimentRow
from app.domain.schemas import MaterialSpec, ProductDomain, Requirement
from app.services import llm
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.db.database import default_session_factory

    url = f"sqlite:///{tmp_path}/hist.db"
    run_upgrade(url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", url)
    get_settings.cache_clear()
    yield default_session_factory()
    get_settings.cache_clear()


def _row(domain: ProductDomain, n: int) -> ExperimentRow:
    return ExperimentRow(
        domain=domain.value, factors={"Zinc phosphate": float(n)}, measured={"salt_spray_hours": 500.0 + n},
        source="lab", label=f"{domain.value}-{n}",
    )


def _run(monkeypatch, req):
    import app.services.kg.formulation_similarity as sim

    seen: list[list[dict]] = []

    def spy(query_factors, all_experiments, **kw):
        seen.append(list(all_experiments))
        return []

    monkeypatch.setattr(sim, "find_similar_formulations", spy)
    monkeypatch.setattr(llm, "complete_structured", lambda *a, **k: (None, "no llm in tests"))
    llm.recommend_formulations(req, n=1)
    return seen


def _req(domain=ProductDomain.anticorrosion_coating) -> Requirement:
    return Requirement(domain=domain, materials=[MaterialSpec(name="Zinc phosphate", weight_pct=5.0)])


def test_only_the_requests_domain_is_loaded(db, monkeypatch):
    with db() as s:
        s.add_all([_row(ProductDomain.anticorrosion_coating, i) for i in range(3)])
        s.add_all([_row(ProductDomain.degreaser, i) for i in range(5)])
        s.commit()

    (exps,) = _run(monkeypatch, _req())

    assert len(exps) == 3 and {e["domain"] for e in exps} == {"anticorrosion_coating"}


def test_scan_is_bounded_to_the_newest_rows(db, monkeypatch):
    monkeypatch.setattr(llm, "_HISTORY_SCAN_LIMIT", 4)
    with db() as s:
        s.add_all([_row(ProductDomain.anticorrosion_coating, i) for i in range(10)])
        s.commit()

    (exps,) = _run(monkeypatch, _req())

    assert len(exps) == 4
    assert sorted(e["factors"]["Zinc phosphate"] for e in exps) == [6.0, 7.0, 8.0, 9.0]


def test_empty_ledger_is_fine(db, monkeypatch):
    assert _run(monkeypatch, _req()) == [[]]
