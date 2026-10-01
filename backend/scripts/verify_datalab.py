#!/usr/bin/env python3
"""C-5 Datalab availability verification (one-shot, run when URL+key exist).

Usage:
    FORMUMIND_DATALAB_API_URL=http://host:5001 DATALAB_API_KEY=... \\
        backend/.venv/bin/python backend/scripts/verify_datalab.py [--item-id ID]

Steps:
  1. check_datalab_reachable (connectivity probe)
  2. optional: fetch one item + extract measurements (needs --item-id)
  3. print a JSON verification report; exit 0 only if reachable.

This script performs NO writes. ``validated`` in the report is true only
when a real Datalab answered; until then the C-5 chain stays honestly
blocked (validated=null in code).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import get_settings  # noqa: E402
from app.db.datalab_client import check_datalab_reachable  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify Datalab ELN reachability (C-5)")
    ap.add_argument("--item-id", default="", help="Datalab item id to probe-read")
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()

    settings = get_settings()
    api_url = settings.datalab_api_url
    report: dict = {
        "api_url": api_url,
        "reachable": False,
        "reason": None,
        "validated": None,
    }

    ok, reason = check_datalab_reachable(api_url, timeout=args.timeout)
    report["reachable"] = ok
    report["reason"] = reason
    if not ok:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            "Datalab 不可达：C-5 真实测量回灌继续 blocked，validated 保持 null。",
            file=sys.stderr,
        )
        return 1

    if args.item_id:
        from app.services.datalab_sync import extract_measurements, fetch_item_data

        item_data = fetch_item_data(api_url, args.item_id, timeout=args.timeout)
        if item_data is None:
            report["reason"] = f"item {args.item_id} read failed"
        else:
            ms = extract_measurements(item_data)
            report["item_id"] = args.item_id
            report["measurements_found"] = len(ms)
            report["validated"] = True

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
