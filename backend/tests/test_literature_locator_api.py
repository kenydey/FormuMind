"""PUT /api/wiki/literature/items/{id}/locator — the missing write path.

``set_item_locator`` (W4-6) validated and stored ``{page, figure, table}`` but
nothing in production called it: the UI said "只读展示，无写入口" and the
artifact-version evidence block that reads the locator was therefore always
empty. The endpoint below is the first real caller.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import literature_manifest as lm

PID = "p-loc"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    get_settings.cache_clear()
    man = lm.empty_manifest(PID)
    man["items"] = [
        lm.ensure_item_library_fields({"id": "a", "title": "Epoxy coating", "doi": "10.1/aaa"}),
        lm.ensure_item_library_fields({"id": "b", "title": "Silane primer", "doi": "10.1/bbb"}),
    ]
    lm.save_manifest(man)
    yield TestClient(app)
    get_settings.cache_clear()


def _put(client: TestClient, item_id: str, **body):
    return client.put(
        f"/api/wiki/literature/items/{item_id}/locator", json={"project_id": PID, **body}
    )


def test_sets_a_locator_and_records_who(client):
    res = _put(client, "a", page=5, figure="2", actor="alice")
    assert res.status_code == 200, res.text

    assert lm.get_item_locator(PID, "a") == {"page": 5, "figure": "2"}
    item = next(i for i in res.json()["items"] if i["id"] == "a")
    assert item["locator"] == {"page": 5, "figure": "2"}
    assert item["locator_by"] == "alice"
    # Other items are untouched.
    assert lm.get_item_locator(PID, "b") is None


def test_blank_fields_are_dropped_and_an_empty_body_clears(client):
    assert _put(client, "a", page=3, table="  ").status_code == 200
    assert lm.get_item_locator(PID, "a") == {"page": 3}

    assert _put(client, "a").status_code == 200  # nothing given → clear
    assert lm.get_item_locator(PID, "a") is None


@pytest.mark.parametrize("bad", [0, -2, "abc", 1.5])
def test_invalid_page_is_rejected(client, bad):
    assert _put(client, "a", page=bad).status_code == 422
    assert lm.get_item_locator(PID, "a") is None


def test_unknown_item_is_404(client):
    assert _put(client, "nope", page=1).status_code == 404


def test_manifest_toggle_off_is_409(client, monkeypatch):
    monkeypatch.setenv("FORMUMIND_LITERATURE_MANIFEST_ENABLED", "false")
    get_settings.cache_clear()
    assert _put(client, "a", page=1).status_code == 409
    assert lm.get_item_locator(PID, "a") is None


def test_locator_does_not_break_a_freeze(client):
    lm.freeze(PID, actor="tester")
    before = lm.load_manifest(PID)["frozen"]["digest"]

    assert _put(client, "a", page=9, figure="4").status_code == 200

    man = lm.load_manifest(PID)
    assert man["frozen"] is not None and man["frozen"]["digest"] == before
    assert lm.get_item_locator(PID, "a") == {"page": 9, "figure": "4"}


def test_locator_reaches_the_evidence_block(client):
    """The consumer that motivated the field: artifact evidence reads it back."""
    from app.domain.citations import CitationLocator

    assert _put(client, "a", page=6, table="1").status_code == 200
    loc = CitationLocator.from_dict(lm.get_item_locator(PID, "a"))
    assert loc is not None and loc.to_dict()["page"] == 6
