"""Production startup self-checks: fail-fast on truly required configuration.

Only checks that are cheap (no network) and deterministic belong here, so the
lifespan manager can run them unconditionally — including under the test
fast-path. Anything optional degrades to a logged warning instead of a hard
failure.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SUPPORTED_DB_SCHEMES = ("sqlite", "postgresql", "postgres")


def _sqlite_file_path(db_url: str) -> Path | None:
    """Return the filesystem path for a sqlite ``db_url``, else None."""
    parsed = urlparse(db_url)
    if parsed.scheme != "sqlite":
        return None
    # sqlite:///./data/formumind.db -> relative "data/formumind.db"
    # sqlite:///data/formumind.db   -> relative "data/formumind.db"
    # sqlite:////abs/path.db        -> absolute "/abs/path.db"
    # sqlite:///:memory:            -> ":memory:" (no filesystem check needed)
    path = parsed.path
    if path.startswith("//"):
        return Path(path[1:])
    if parsed.netloc in ("", ".", "localhost") and path.startswith("/"):
        path = path[1:]
    elif parsed.netloc and parsed.netloc not in (".", "localhost"):
        path = f"//{parsed.netloc}{path}"
    return Path(path or "formumind.db")


def run_startup_checks(settings) -> list[str]:
    """Validate required startup configuration.

    Raises:
        RuntimeError: on a hard misconfiguration, with an actionable message
            naming the variable and where to fix it.
    Returns:
        A list of non-fatal warning strings (caller logs them).
    """
    warnings: list[str] = []
    errors: list[str] = []

    # 1. db_url must use a supported scheme.
    db_url = (getattr(settings, "db_url", "") or "").strip()
    scheme = urlparse(db_url).scheme.lower()
    if scheme not in _SUPPORTED_DB_SCHEMES:
        errors.append(
            f"FORMUMIND_DB_URL has unsupported scheme {scheme!r} "
            f"(url={db_url!r}); supported: sqlite://, postgresql://. "
            "Fix: set FORMUMIND_DB_URL in .env or the environment."
        )

    # 2. sqlite: the database directory must exist and be writable.
    sqlite_path = _sqlite_file_path(db_url) if not errors else None
    if sqlite_path is not None:
        parent = sqlite_path.parent.resolve()
        if not parent.is_dir():
            errors.append(
                f"SQLite data directory {parent} does not exist "
                f"(from FORMUMIND_DB_URL={db_url!r}). "
                "Fix: create it, or mount the data volume (see docs/deployment.md)."
            )
        elif not os.access(parent, os.W_OK):
            errors.append(
                f"SQLite data directory {parent} is not writable "
                f"(from FORMUMIND_DB_URL={db_url!r}). "
                "Fix: check ownership/permissions or the container volume mount."
            )

    # 3. ELN-mandatory deploys must point at a real Datalab URL.
    if getattr(settings, "datalab_required", False):
        url = (getattr(settings, "datalab_api_url", "") or "").strip()
        if not url:
            errors.append(
                "FORMUMIND_DATALAB_REQUIRED=true but FORMUMIND_DATALAB_API_URL "
                "is empty. Fix: set the Datalab URL in .env, or set "
                "FORMUMIND_DATALAB_REQUIRED=false to fall back to the local ledger."
            )

    # 4. Auth explicitly disabled in production is almost certainly a mistake
    #    (config auto-enables it for production envs).
    env = (getattr(settings, "environment", "") or "").strip().lower()
    if env in ("production", "prod") and getattr(settings, "api_auth_enabled", None) is False:
        warnings.append(
            "FORMUMIND_API_AUTH_ENABLED=false with FORMUMIND_ENVIRONMENT=production: "
            "the API is unauthenticated. Set a FORMUMIND_API_TOKEN and enable auth "
            "unless this host is truly isolated."
        )

    if errors:
        raise RuntimeError("Startup configuration check failed:\n- " + "\n- ".join(errors))
    return warnings
