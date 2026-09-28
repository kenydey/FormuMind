"""B-10 回归：screening evaluate 的 year_min/year_max 非法值必须映射 400，
此前 _predict 内的 int(year_min) 无 try → 500（同链路另三个端点都映射
ValueError→400，唯独 evaluate 漏了）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import literature_manifest as lm
from app.services import literature_screening as ls


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "progress"))
    return tmp_path


class _S:
    literature_manifest_enabled = True
    literature_screening_enabled = True
    screening_auto_freeze = False
    screening_async_threshold = 500
    celery_eager = True


def _seed_labeled(pid: str = "pe"):
    man = lm.empty_manifest(pid)
    items = [
        ("a1", "Epoxy coating corrosion test", "epoxy salt spray", "match"),
        ("a2", "Polyurethane topcoat weathering", "polyurethane UV", "match"),
        ("a3", "Zinc-rich primer adhesion", "zinc-rich steel", "match"),
        ("a4", "Waterborne epoxy primer", "水性 epoxy", "match"),
        ("b1", "Mouse genome study", "biology", "no_match"),
        ("b2", "Drug delivery nanoparticles", "pharma", "no_match"),
    ]
    man["items"] = [
        {
            "id": i, "title": t, "snippet": s, "doi": None,
            "evidence_class": "search_hit",
            "screening": lbl, "screening_source": "human",
        }
        for i, t, s, lbl in items
    ]
    lm.save_manifest(man)


def test_evaluate_invalid_year_min_maps_400(tmp_data):
    _seed_labeled()
    with pytest.raises(HTTPException) as ei:
        ls.evaluate_screening(
            "pe",
            {"include_keywords": ["epoxy"], "year_min": "not-a-year"},
            settings=_S(),
        )
    assert ei.value.status_code == 400


def test_evaluate_invalid_year_max_maps_400(tmp_data):
    _seed_labeled()
    with pytest.raises(HTTPException) as ei:
        ls.evaluate_screening(
            "pe",
            {"include_keywords": ["epoxy"], "year_max": "20xx"},
            settings=_S(),
        )
    assert ei.value.status_code == 400


def test_evaluate_valid_year_string_still_works(tmp_data):
    _seed_labeled()
    out = ls.evaluate_screening(
        "pe",
        {"include_keywords": ["epoxy"], "year_min": "2019", "year_max": 2030},
        settings=_S(),
    )
    assert out["evaluated"] is True
