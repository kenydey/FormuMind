"""Live smoke test of the EPO OPS patent search with *your* credentials.

The patent search calls EPO OPS directly (``app/services/epo_ops.py``) instead of through ``patent-client``. Its
requests, error handling and parser are covered by offline tests (``tests/test_epo_ops.py``,
``tests/test_epo_patent_search.py``) built from the OPS reference guide and from error bodies recorded from the live
service - but the *authenticated* search needs a consumer key, and none was available when it was written. This is the
check to run once you have one (free registration: https://developers.epo.org):

    cd backend
    FORMUMIND_EPO_CONSUMER_KEY=... FORMUMIND_EPO_CONSUMER_SECRET=... \\
        python ../scripts/audit/epo_ops_smoke.py ["magnesium alloy corrosion"] [--cpc C23C22] [--raw]

(or leave the two variables out when they are already in ``backend/.env`` / the Settings page.) It runs, in order:

1. the token request - proves the key / secret pair is accepted;
2. one page of bibliographic search results, printed as parsed;
3. the provider the application actually calls (``search_providers.search_epo_patents``), printed as evidence;
4. with ``--raw``: the status line, the throttling / quota headers and the first 3,000 characters of the raw reply,
   which is what to attach to a bug report when the parser finds nothing in a reply that clearly has hits.

Nothing printed contains the key or the secret. Exit status 0 when every step worked and the default query found
patents, 1 otherwise (the first failing step says why).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.getcwd())  # backend/: the app package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the EPO OPS patent search with real credentials.")
    parser.add_argument("query", nargs="?", default="magnesium alloy corrosion")
    parser.add_argument("--cpc", action="append", default=[], help="CPC class to filter by (repeatable, at most two used)")
    parser.add_argument("--raw", action="store_true", help="also print the raw first reply (status, quota headers, body)")
    parser.add_argument("--pages", type=int, default=1, help="pages of 25 to fetch in step 2 (default 1)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    from app.config import get_settings
    from app.services import epo_ops
    from app.services.http_safe import make_client
    from app.services.runtime_secrets import effective_setting
    from app.services.search_providers import search_epo_patents

    settings = get_settings()
    key = effective_setting(settings, "epo_consumer_key")
    secret = effective_setting(settings, "epo_consumer_secret")
    if not key or not secret:
        print("No EPO credentials: set FORMUMIND_EPO_CONSUMER_KEY and FORMUMIND_EPO_CONSUMER_SECRET.", file=sys.stderr)
        return 1

    print("1. token request ...")
    try:
        ttl = epo_ops.check_credentials(key, secret)
    except epo_ops.EpoOpsError as exc:
        print(f"   FAILED ({exc.kind}): {exc}", file=sys.stderr)
        return 1
    print(f"   ok - the token lives {int(ttl)} s")

    cql = epo_ops.build_cql(args.query, args.cpc)
    print(f"2. search: {cql}")
    if cql is None:
        print("   nothing searchable in that query (OPS takes ASCII words; Chinese goes to the CNIPA providers)", file=sys.stderr)
        return 1
    found = 0
    try:
        for page in range(max(1, args.pages)):
            begin = 1 + page * epo_ops.PAGE_SIZE
            result = epo_ops.search(cql, key=key, secret=secret, begin=begin, end=begin + epo_ops.PAGE_SIZE - 1)
            if page == 0:
                print(f"   {result.total} results in total")
            for hit in result.hits:
                found += 1
                print(f"   {hit.publication_number:<18} {hit.pub_date or '----------'}  {hit.applicant[:28]:<28}  {hit.title[:70]}")
                if hit.abstract:
                    print(f"      {hit.abstract[:110]}")
            if len(result.hits) < epo_ops.PAGE_SIZE:
                break
    except epo_ops.EpoOpsError as exc:
        print(f"   FAILED ({exc.kind}): {exc}", file=sys.stderr)
        if exc.kind == "bad_query":
            print("   (the query was rejected: if the server says the index is unknown, build_cql's `ta` needs changing)", file=sys.stderr)
        return 1
    if found == 0:
        print("   0 hits parsed - run again with --raw to see what OPS sent", file=sys.stderr)

    print("3. the provider the application calls ...")
    evidence = search_epo_patents(args.query, limit=5, cpc_codes=args.cpc)
    print(f"   {len(evidence)} evidence rows" + (": " + ", ".join(e.identifier for e in evidence) if evidence else ""))

    if args.raw:
        print("4. raw reply (first page, 3 results)")
        with make_client(timeout=20.0) as client:
            token = epo_ops.access_token(client, key, secret)
            resp = epo_ops._get(client, token, cql, 1, 3)
        print(f"   HTTP {resp.status_code}")
        for header in ("content-type", "x-throttling-control", "x-individualquotaperhour-used", "x-registeredquotaperweek-used", "x-rejection-reason"):
            if header in resp.headers:
                print(f"   {header}: {resp.headers[header]}")
        print(resp.text[:3000])

    ok = found > 0 and bool(evidence)
    print("OK" if ok else "NOT OK: the chain ran but produced nothing - see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
