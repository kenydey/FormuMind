"""Top-5″ #2: Datalab soft-degrade defaults (auto + REQUIRED=false → local ledger)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import campaign_store as cs_mod
from app.db import store as store_mod
from app.db.campaign_store import SqliteCampaignStore, get_campaign_store, reset_campaign_store
from app.db.store import SqlExperimentStore, get_experiment_store, reset_experiment_store
from app.main import app
from app.services.env_flags import FLAG_REGISTRY


@pytest.fixture(autouse=True)
def _reset():
    reset_campaign_store(None)
    reset_experiment_store(None)
    get_settings.cache_clear()
    yield
    reset_campaign_store(None)
    reset_experiment_store(None)
    get_settings.cache_clear()


def test_soft_degrade_product_defaults():
    """Field defaults (ignore conftest sqlite env override)."""
    fields = Settings.model_fields
    assert fields["datalab_required"].default is False
    assert fields["campaign_backend"].default == "auto"
    assert fields["experiment_backend"].default == "auto"
    assert fields["wiki_dossier_auto_patch"].default is False


def test_datalab_required_flag_copy_mentions_soft_degrade():
    flag = next(f for f in FLAG_REGISTRY if f.attr == "datalab_required")
    assert "本地台账" in flag.description or "soft" in flag.description.lower()


def test_campaign_auto_unreachable_soft_falls_back_sqlite(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "auto")
    monkeypatch.setenv("FORMUMIND_EXPERIMENT_BACKEND", "auto")
    monkeypatch.setenv("FORMUMIND_DATALAB_REQUIRED", "false")
    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://datalab.test:5001")
    get_settings.cache_clear()
    monkeypatch.setattr(
        cs_mod, "check_datalab_reachable", lambda url, timeout=2.0: (False, "down")
    )
    store = get_campaign_store()
    assert isinstance(store, SqliteCampaignStore)


def test_experiment_auto_unreachable_soft_falls_back_sqlite(monkeypatch):
    s = get_settings().model_copy(
        update={
            "experiment_backend": "auto",
            "campaign_backend": "auto",
            "datalab_required": False,
            "datalab_api_url": "http://datalab.test:5001",
        }
    )
    monkeypatch.setattr(
        store_mod, "check_datalab_reachable", lambda url, timeout=2.0: (False, "down")
    )
    store = get_experiment_store(s)
    assert isinstance(store, SqlExperimentStore)


def test_health_soft_local_ledger_when_optional_down(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "auto")
    monkeypatch.setenv("FORMUMIND_EXPERIMENT_BACKEND", "auto")
    monkeypatch.setenv("FORMUMIND_DATALAB_REQUIRED", "false")
    monkeypatch.setenv("FORMUMIND_DATALAB_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("FORMUMIND_CELERY_EAGER", "true")
    get_settings.cache_clear()
    client = TestClient(app)
    body = client.get("/health").json()
    # Soft-degrade: ELN down must NOT force overall degraded by itself.
    assert body["datalab"]["required"] is False
    assert body["datalab"]["reachable"] is False
    assert body["datalab"]["ledger_mode"] == "local"
    assert "本地" in (body["datalab"].get("hint") or "")
    assert body["datalab"]["campaign_backend"] == "auto"
