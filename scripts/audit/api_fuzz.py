"""Schema-guided API fuzz (round-4): hostile-but-type-plausible mutations of every documented operation.

For each operation the OpenAPI smoke walk's minimal request (``backend/tests/test_openapi_smoke_walk.py``) is mutated
leaf by leaf with values that real clients and hostile ones send — empty / 20 KB / NUL-containing / injection-looking
strings, 0 / -1 / 2**63 / 1e308 / NaN / Infinity numbers, wrong JSON types, empty and 3000-element lists, nulls — plus
ids too large for the database, NUL and 3000-character path segments, and junk ``limit`` / ``page`` / ``offset``. Task
dispatch is stubbed (as in the walk) so the HTTP layer is what is exercised, and outbound network is denied (the suite's
``tests/_network_guard.py``): the fuzzer used to send its hostile input to the real SureChEMBL, OpenAlex, DuckDuckGo and
USPTO, and went red whenever one of them answered slowly. Anything that answers 5xx other than 503 (a feature switched
off or a dependency down) or does not answer within 20 s is a defect.

Run from ``backend/``::

    python ../scripts/audit/api_fuzz.py [SEED ...] [--limit=N]

Found, in about 40,000 requests: 20 endpoints answering 500 for an id above 2**63, and seven answering 500 for
``Infinity`` / ``NaN`` in a numeric field (both pinned by ``tests/test_request_robustness.py``). Exit status 1 when
anything is found.
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import copy
import json
import logging
import math
import os
import random
import re
import sys
import tempfile
import time
import uuid
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.getcwd())  # backend/: the app and the ``tests`` package
TMP = tempfile.mkdtemp(prefix="apifuzz-")
os.environ.update(
    {
        "FORMUMIND_ENVIRONMENT": "test",
        "FORMUMIND_API_AUTH_ENABLED": "false",
        "FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP": "1",
        "FORMUMIND_CELERY_EAGER": "true",
        "FORMUMIND_CAMPAIGN_BACKEND": "sqlite",
        "FORMUMIND_EXPERIMENT_BACKEND": "sqlite",
        "FORMUMIND_DATALAB_REQUIRED": "false",
        "FORMUMIND_KB_INGEST_AUTO": "false",
        "FORMUMIND_DB_URL": f"sqlite:///{TMP}/fuzz.db",
        "FORMUMIND_DATA_DIR": TMP,
        "FORMUMIND_ENV_FILE": f"{TMP}/.env",
    }
)

os.chdir(TMP)  # relative ``./data`` writes land in the throw-away directory, not in the checkout

from tests import _network_guard  # noqa: E402

_network_guard.install()  # before the app: nothing here may reach a third-party service or wait on one

import app.api._dispatch as dispatch  # noqa: E402
from app.db.database import Base, default_engine  # noqa: E402
from app.domain.project_workspace import default_requirement  # noqa: E402
from app.main import app  # noqa: E402
from app.middleware.rate_limit import reset_rate_limits  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.test_openapi_smoke_walk import _operations, _request_for  # noqa: E402

Base.metadata.create_all(default_engine())
dispatch._submit_eager_background = lambda task, payload, kind, *, task_id=None: task_id or str(uuid.uuid4())
client = TestClient(app, raise_server_exceptions=False)
requirement = json.loads(default_requirement().model_dump_json())

EVIL_STR = [
    "", " ", "a" * 20000, "盐雾\u0000", "'; DROP TABLE experiments;--", "../../etc/passwd", "{{7*7}}${7*7}",
    "‮﻿", "null", "<script>alert(1)</script>", "%s%n%x", "\n\r\t", "0", "-1", "NaN", "Infinity", "9" * 400,
]
EVIL_NUM = [0, -1, 1, 2**31, 2**63, -(2**63), 1e308, -1e308, 1e-308, float("nan"), float("inf"), float("-inf"), 0.5]
REQUEST_BUDGET_S = 20


def mutate_value(rng: random.Random, v):
    if isinstance(v, bool):
        return rng.choice([True, False, None, "true", 1])
    if isinstance(v, (int, float)):
        return rng.choice(EVIL_NUM + [None, "1", "abc", [], {}])
    if isinstance(v, str):
        return rng.choice(EVIL_STR + [None, 0, [], {}, True])
    if isinstance(v, list):
        return rng.choice([[], [None], v * 50, ["x"] * 3000, [[]], None, "x", {}, [v[0]] * 400 if v else [1]])
    if isinstance(v, dict):
        return rng.choice([{}, None, [], "x", {"": ""}, {k: None for k in v}, {"__proto__": 1}])
    return rng.choice(EVIL_STR + EVIL_NUM)


def leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, path + (k,))
        yield path, obj
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            yield from leaves(v, path + (i,))
        yield path, obj
    else:
        yield path, obj


def set_path(obj, path, value):
    if not path:
        return value
    cur = obj
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value
    return obj


def _finite(o) -> bool:
    if isinstance(o, float):
        return math.isfinite(o)
    if isinstance(o, dict):
        return all(_finite(v) for v in o.values())
    if isinstance(o, list):
        return all(_finite(v) for v in o)
    return True


def call(method: str, url: str, kwargs: dict):
    if "json" in kwargs and not _finite(kwargs["json"]):
        # httpx refuses non-finite floats in ``json=``; a hostile client sends the literals as raw bytes
        kwargs = dict(kwargs)
        body = kwargs.pop("json")
        kwargs["content"] = json.dumps(body, allow_nan=True).encode()
        kwargs["headers"] = {"content-type": "application/json"}
    pool = cf.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(lambda: getattr(client, method)(url, **kwargs))
    try:
        return future.result(timeout=REQUEST_BUDGET_S)
    except cf.TimeoutError:
        return "HANG"
    except Exception as exc:  # noqa: BLE001
        return exc
    finally:
        pool.shutdown(wait=False)


def variants_for(rng: random.Random, method: str, path: str, op: dict, limit: int):
    base_url, base = _request_for(method, path, op, requirement)
    if method == "delete" and "json" in base:
        return base_url, []
    out = []
    if base.get("json") is not None:
        body = base["json"]
        pool = list(leaves(body))
        for _ in range(limit):
            leaf_path, leaf = rng.choice(pool)
            new = mutate_value(rng, leaf)
            mutated = copy.deepcopy(body)
            try:
                mutated = set_path(mutated, leaf_path, new)
            except Exception:  # noqa: BLE001
                continue
            out.append(("json", leaf_path + (("=", repr(new)[:40]),), {**base, "json": mutated}, base_url))
        for bad in ([], "x", 5, None, {"unexpected": [1, 2, 3]}):
            out.append(("json-root", (repr(bad)[:20],), {**base, "json": bad}, base_url))
    for name, value in list((base.get("params") or {}).items()):
        for _ in range(4):
            new = mutate_value(rng, value)
            out.append(("param", (name, ("=", repr(new)[:40])), {**base, "params": {**base["params"], name: new}}, base_url))
    junk = {**(base.get("params") or {}), "limit": rng.choice(["-1", "0", "99999999", "abc"]),
            "page": rng.choice(["-1", "0", "abc"]), "offset": "-5"}
    out.append(("junk-query", (), {**base, "params": junk}, base_url))
    for url in {
        base_url,
        re.sub(r"/1(?=/|$)", "/" + "9" * 30, base_url),
        re.sub(r"/1(?=/|$)", "/%00", base_url),
        re.sub(r"/x(?=/|$)", "/" + "a" * 3000, base_url),
    }:
        out.append(("url", (url[:60],), dict(base), url))
    return base_url, out


def run(seed: int, limit: int) -> tuple[int, dict, list]:
    rng = random.Random(seed)
    failures: dict = collections.OrderedDict()
    hangs: list = []
    sent = 0
    for method, path, op in list(_operations()):
        _base_url, variants = variants_for(rng, method, path, op, limit)
        for kind, where, kwargs, url in variants:
            reset_rate_limits()
            sent += 1
            result = call(method, url, kwargs)
            if result == "HANG":
                hangs.append((method.upper(), path, kind, where))
                continue
            if isinstance(result, Exception):
                failures.setdefault((method.upper(), path, type(result).__name__), (kind, where, repr(result)[:160]))
            elif result.status_code >= 500 and result.status_code != 503:
                failures.setdefault((method.upper(), path, result.status_code), (kind, where, result.text[:160]))
    return sent, failures, hangs


def main() -> int:
    seeds = [int(a) for a in sys.argv[1:] if not a.startswith("--")] or [1, 2, 3]
    limit = int(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--limit=")), 12))
    problems = 0
    print(f"operations: {len(list(_operations()))}, seeds: {seeds}, mutations per operation: {limit}", flush=True)
    for seed in seeds:
        started = time.time()
        sent, failures, hangs = run(seed, limit)
        print(
            f"seed {seed}: sent {sent} requests in {time.time() - started:.0f}s; "
            f"distinct 5xx/exception endpoints: {len(failures)}; hangs: {len(hangs)}",
            flush=True,
        )
        for key, value in failures.items():
            print("  ", key, value)
        for hang in hangs:
            print("   HANG", hang)
        problems += len(failures) + len(hangs)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
