"""Post-A′ #4: project-level prediction_bias_soft_correct OR global."""
from __future__ import annotations

import pytest

from app.config import Settings, get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.domain.project_workspace import ProjectWorkspace
from app.domain.schemas import ProductDomain, Requirement
from app.services.prediction_bias_correct import soft_correct_predicted


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_PREDICTION_BIAS_SOFT_CORRECT", "false")
    get_settings.cache_clear()

    db_path = tmp_path / "proj_sc.db"
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    projects = ProjectStore(factory)
    monkeypatch.setattr(ps_mod, "_store", projects)
    yield {"projects": projects}
    get_settings.cache_clear()


def test_global_default_still_off():
    assert Settings.model_fields["prediction_bias_soft_correct"].default is False


def test_workspace_field_defaults_false():
    ws = ProjectWorkspace()
    assert ws.prediction_bias_soft_correct is False


def test_project_flag_enables_without_global(env, monkeypatch):
    monkeypatch.setattr(
        "app.services.prediction_bias_correct.get_settings",
        lambda: Settings(prediction_bias_soft_correct=False, prediction_bias_soft_correct_min_n=1),
    )
    projects: ProjectStore = env["projects"]
    detail = projects.create(
        title="soft-correct",
        requirement=Requirement(
            domain=ProductDomain.anticorrosion_coating,
            substrate="carbon_steel",
        ),
    )
    ws = detail.workspace.model_copy(update={"prediction_bias_soft_correct": True})
    projects.update(detail.id, ws.model_dump(mode="json"))

    bias = {"salt_spray_hours": {"n": 5, "mean_error": 10.0}}
    out, metrics = soft_correct_predicted(
        {"salt_spray_hours": 100.0},
        project_id=detail.id,
        by_metric=bias,
    )
    assert metrics == ["salt_spray_hours"]
    assert out["salt_spray_hours"] == 90.0


def test_explicit_enabled_false_overrides(monkeypatch):
    monkeypatch.setattr(
        "app.services.prediction_bias_correct.get_settings",
        lambda: Settings(prediction_bias_soft_correct=True, prediction_bias_soft_correct_min_n=1),
    )
    out, metrics = soft_correct_predicted(
        {"m": 1.0},
        by_metric={"m": {"n": 5, "mean_error": 0.5}},
        enabled=False,
    )
    assert metrics == []
    assert out["m"] == 1.0
