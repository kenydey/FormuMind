"""W5: auto_adopt_next_doe_on_loop flag defaults off and is registered."""
from __future__ import annotations

from app.config import Settings, get_settings
from app.services.env_flags import FLAG_REGISTRY


def test_auto_adopt_flag_defaults_false():
    assert Settings().auto_adopt_next_doe_on_loop is False
    assert get_settings().auto_adopt_next_doe_on_loop is False


def test_auto_adopt_flag_in_env_registry():
    attrs = {f.attr for f in FLAG_REGISTRY}
    assert "auto_adopt_next_doe_on_loop" in attrs
    flag = next(f for f in FLAG_REGISTRY if f.attr == "auto_adopt_next_doe_on_loop")
    assert flag.category == "data"
    assert flag.env_key == "FORMUMIND_AUTO_ADOPT_NEXT_DOE_ON_LOOP"
