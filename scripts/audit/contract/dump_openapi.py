"""Dump the backend OpenAPI schema (every route, including hidden ones) for compare.py.

Usage:  OUT=scripts/audit/contract/data/openapi.json python scripts/audit/contract/dump_openapi.py
Run from the repository root with the backend importable (``pip install -e backend[dev]``).
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("FORMUMIND_ENVIRONMENT", "test")
os.environ.setdefault("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")

from app.main import app  # noqa: E402


def walk(routes):
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            walk(route.effective_candidates())
            continue
        if hasattr(route, "include_in_schema"):
            route.include_in_schema = True


walk(app.routes)
app.openapi_schema = None
spec = app.openapi()
out = pathlib.Path(os.environ.get("OUT", ROOT / "scripts/audit/contract/data/openapi.json"))
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(spec, indent=1), encoding="utf-8")
print("paths", len(spec["paths"]), "schemas", len(spec["components"]["schemas"]), "->", out)
