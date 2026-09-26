"""OpenAlex / Unpaywall mailto must not default to a personal mailbox."""
from __future__ import annotations

from app.config import Settings


def test_openalex_mailto_default_is_placeholder():
    field = Settings.model_fields["openalex_mailto"]
    assert field.default == "formumind@example.com"
    assert "kenydey" not in str(field.default).lower()


def test_unpaywall_mailto_default_is_placeholder():
    field = Settings.model_fields["unpaywall_mailto"]
    assert field.default == "formumind@example.com"
    assert "kenydey" not in str(field.default).lower()
