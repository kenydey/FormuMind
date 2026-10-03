#!/usr/bin/env python3
"""One-shot health sweep over FormuMind's operations endpoints (API-only on purpose).

The diagnostics below have no screen: they are for whoever runs the deployment. This script
reads them all and prints one page, so "is the knowledge base healthy / is DataLab leaking
samples / are recommendations being adopted" is a single command instead of five `curl`s.

    python scripts/ops_check.py                                   # http://localhost:8000
    python scripts/ops_check.py --base-url https://formumind.example --token "$FORMUMIND_API_TOKEN"
    python scripts/ops_check.py --strict                          # non-zero exit on findings (cron / CI)

Exit status: 0 = reachable and clean, 1 = an endpoint could not be read, 2 = findings
(only with ``--strict``: dead DataLab orphans, an unavailable KB index).

Standard library only, so it runs anywhere the API is reachable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

Fetch = Callable[[str], Any]

# (title, path) — every route listed here is API-only by design (see tests/test_frontend_api_wiring.py).
PROBES: tuple[tuple[str, str], ...] = (
    ("KB health", "/api/ops/kb-health"),
    ("Evidence compression", "/api/ops/evidence-stats"),
    ("Recommendation adoption (7 d)", "/api/ops/recommend-stats?days=7"),
    ("Relevance shadow", "/api/kb/relevance-shadow/stats"),
    ("DataLab orphan cleanup", "/api/ops/datalab-orphans"),
)


def make_fetch(base_url: str, token: str | None, timeout: float = 15.0) -> Fetch:
    def fetch(path: str) -> Any:
        request = urllib.request.Request(base_url.rstrip("/") + path)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied URL
            return json.loads(response.read().decode("utf-8"))

    return fetch


def _summary(value: Any, limit: int = 6) -> list[str]:
    """Top-level scalars of a response, ``key: value`` — enough to read at a glance."""
    if not isinstance(value, dict):
        return [str(value)[:200]]
    rows = []
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            rows.append(f"{key}: {item}")
        elif isinstance(item, dict) and all(isinstance(v, (int, float, str, bool, type(None))) for v in item.values()):
            rows.append(f"{key}: " + ", ".join(f"{k}={v}" for k, v in list(item.items())[:8]))
        if len(rows) >= limit:
            break
    return rows


def run_checks(fetch: Fetch, *, strict: bool = False) -> tuple[list[str], int]:
    """Read every probe; return the report lines and the exit status."""
    lines: list[str] = []
    unreadable = 0
    findings: list[str] = []

    for title, path in PROBES:
        lines.append(f"== {title}  ({path})")
        try:
            body = fetch(path)
        except Exception as exc:  # noqa: BLE001 - report and carry on with the other probes
            unreadable += 1
            lines.append(f"   UNREADABLE: {type(exc).__name__}: {exc}")
            continue
        lines.extend(f"   {row}" for row in _summary(body))

        if path.endswith("/kb-health") and isinstance(body, dict) and body.get("available") is False:
            findings.append("KB index reports available=false")
        if path.endswith("/datalab-orphans") and isinstance(body, dict):
            counts = body.get("counts") or {}
            if counts.get("DEAD"):
                findings.append(f"{counts['DEAD']} DataLab sample(s) could not be deleted and need manual cleanup")
            if counts.get("PENDING"):
                lines.append(
                    f"   NOTE: {counts['PENDING']} sample(s) still queued — "
                    "POST /api/ops/datalab-orphans/cleanup retries them now"
                )

    lines.append("")
    if findings:
        lines.append("FINDINGS:")
        lines.extend(f"  - {item}" for item in findings)
    if unreadable:
        lines.append(f"{unreadable} endpoint(s) could not be read")
    if not findings and not unreadable:
        lines.append("OK")

    if unreadable:
        return lines, 1
    return lines, 2 if (strict and findings) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default=os.environ.get("FORMUMIND_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("FORMUMIND_API_TOKEN"), help="bearer token (default: $FORMUMIND_API_TOKEN)")
    parser.add_argument("--strict", action="store_true", help="exit 2 when something needs attention")
    args = parser.parse_args(argv)

    try:
        with urllib.request.urlopen(args.base_url.rstrip("/") + "/health", timeout=5):  # noqa: S310
            pass
    except (urllib.error.URLError, OSError) as exc:
        print(f"cannot reach {args.base_url}: {exc}", file=sys.stderr)
        return 1

    lines, status = run_checks(make_fetch(args.base_url, args.token), strict=args.strict)
    print("\n".join(lines))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
