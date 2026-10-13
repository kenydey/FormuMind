"""Base class for the partial ``Settings`` stand-ins tests hand to code under test.

Production code reads ``settings.<field>`` directly, so a stub must answer for every field it is asked about. Subclass
this and override only what the test cares about; anything else comes back with its real ``Settings`` default.
"""
from __future__ import annotations

from app.config import Settings

_DEFAULTS = Settings.model_construct()  # field defaults only: no .env, no environment


class SettingsDefaults:
    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(_DEFAULTS, name)
