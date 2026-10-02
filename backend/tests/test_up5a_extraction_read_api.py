"""Up-5A: extraction_tables / extraction_formulas 只读 API 回归测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.extraction_store import ExtractionStore
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.db.database as db_mod

    db = tmp_path / "extract.db"
    engine = make_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    store = ExtractionStore(factory)
    store.replace_tables(
        "src-1",
        [
            {
                "page_no": 3,
                "bbox": [0.1, 0.2, 0.9, 0.8],
                "caption": "表1 配方",
                "markdown_text": "| a | b |\n|---|---|\n| 1 | 2 |",
                "n_rows": 2,
                "n_cols": 2,
            }
        ],
    )
    store.replace_formulas(
        "src-1",
        [{"page_no": 4, "latex": "x^2 + y^2", "formula_no": "3"}],
    )
    get_settings.cache_clear()
    return TestClient(app)


def test_source_tables_read_api(client):
    r = client.get("/api/kb/sources/src-1/tables")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["page_no"] == 3
    assert items[0]["caption"] == "表1 配方"
    assert "| a | b |" in items[0]["markdown_text"]
    assert items[0]["n_rows"] == 2 and items[0]["n_cols"] == 2


def test_source_formulas_read_api(client):
    r = client.get("/api/kb/sources/src-1/formulas")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["latex"] == "x^2 + y^2"
    assert items[0]["formula_no"] == "3"


def test_source_tables_empty_for_unknown_source(client):
    r = client.get("/api/kb/sources/nope/tables")
    assert r.status_code == 200
    assert r.json() == []
    r = client.get("/api/kb/sources/nope/formulas")
    assert r.status_code == 200
    assert r.json() == []


def test_bad_bbox_degrades_to_none_not_batch_loss():
    # F-5 回归：单条非法 bbox（如 MinerU 像素坐标）降级为 None，
    # 不能抛错导致整批表格/公式丢失。
    from app.db.db_common import safe_bbox, validate_bbox

    assert safe_bbox([0.1, 0.2, 0.9, 0.8]) == [0.1, 0.2, 0.9, 0.8]
    assert safe_bbox(None) is None
    assert safe_bbox([10, 20, 900, 800]) is None  # 像素坐标 → 非法 → None
    assert safe_bbox("bad") is None
    # validate_bbox 本体仍抛错（显式校验语义不变）
    import pytest

    with pytest.raises(ValueError):
        validate_bbox([10, 20, 900, 800])
