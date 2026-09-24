"""Unit tests for Datalab search → local campaign/row id mapping."""

from __future__ import annotations


def test_parse_datalab_search_maps_item_id_from_sample_refs(tmp_path, monkeypatch):
    from app.api.experiments import _parse_datalab_search
    from app.config import get_settings
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.models import Campaign

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()

    engine = make_engine(f"sqlite:///{tmp_path}/search_map.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    with factory() as session:
        camp = Campaign(
            name="map-camp",
            strategy="test",
            status="IN_PROGRESS",
            sample_refs=[
                {
                    "id": 7,
                    "item_id": "formumind_c42_r7_deadbeef",
                    "status": "Completed",
                    "planned_params": {"GPTMS": 2.0},
                    "measurements": {"salt_spray_hours": 700},
                }
            ],
        )
        session.add(camp)
        session.commit()
        cid = camp.id

    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)

    body = [
        {
            "item_id": "formumind_c42_r7_deadbeef",
            "status": "Completed",
            "blocks_obj": {
                "formumind_params": {
                    "data": {
                        "planned_params": {"GPTMS": 2.0},
                        "status": "Completed",
                    }
                },
                "formumind_measurements": {"data": {"salt_spray_hours": 700}},
            },
        }
    ]
    hits = _parse_datalab_search(body)
    assert len(hits) == 1
    assert hits[0].campaign_id == cid
    assert hits[0].row_id == 7
    assert hits[0].campaign_name == "map-camp"
    assert hits[0].item_id == "formumind_c42_r7_deadbeef"
    assert hits[0].planned_params.get("GPTMS") == 2.0


def test_parse_datalab_search_parses_item_id_pattern_without_refs(tmp_path, monkeypatch):
    from app.api.experiments import _parse_datalab_search
    from app.config import get_settings
    from app.db.database import Base, make_engine, make_session_factory

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/search_pat.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    import app.db.database as db_mod

    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)

    hits = _parse_datalab_search(
        [{"item_id": "formumind_c99_r3_abcdef01", "blocks_obj": {}}]
    )
    assert hits[0].campaign_id == 99
    assert hits[0].row_id == 3
