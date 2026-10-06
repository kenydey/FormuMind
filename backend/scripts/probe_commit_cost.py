"""What a commit, a new file and a rename cost on this machine's temp disk (a diagnostic, not a test).

Windows CI runners spent 3 to 26 seconds in the *setup* of tests that build a fresh SQLite database, and the cause was a
guess: each of the ~110 DDL statements committing (fsyncing) on its own. This puts numbers on it where it matters - on the
runner itself - so the next change is made on what was measured:

    python scripts/probe_commit_cost.py

Prints one line per measurement: the time for N CREATE TABLEs, each in its own transaction, under each ``synchronous``
mode, then all of them in one transaction; then N small files created and N ``os.replace`` calls (what the task-progress
files do, and where a virus scanner shows up).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

N = 150


def _timed(label: str, fn) -> float:
    t0 = time.perf_counter()
    fn()
    dt = time.perf_counter() - t0
    print(f"{label:58s} {dt * 1000:9.0f} ms  ({dt * 1000 / N:6.2f} ms each)")
    return dt


def _tables(path: Path, sync: str | None, one_transaction: bool) -> None:
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    if sync:
        con.execute(f"PRAGMA synchronous={sync}")
    if one_transaction:
        con.execute("BEGIN")
    for i in range(N):
        con.execute(f"CREATE TABLE t{i} (id INTEGER PRIMARY KEY, a TEXT, b REAL)")
        con.execute(f"CREATE INDEX ix{i} ON t{i} (a)")
    if one_transaction:
        con.execute("COMMIT")
    con.close()


def main() -> int:
    print(f"python {sys.version.split()[0]} on {sys.platform}; sqlite {sqlite3.sqlite_version}; N={N}")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for sync in ("FULL", "NORMAL", "OFF"):
            _timed(f"{N} tables+indexes, one commit each, synchronous={sync}", lambda s=sync: _tables(base / f"a_{s}.db", s, False))
        _timed(f"{N} tables+indexes, ONE transaction, synchronous=FULL", lambda: _tables(base / "one.db", "FULL", True))

        files = base / "files"
        files.mkdir()

        def create() -> None:
            for i in range(N):
                (files / f"f{i}.json").write_text('{"k": %d}' % i, encoding="utf-8")

        def replace() -> None:
            for i in range(N):
                tmp_path = files / f".f{i}.tmp"
                tmp_path.write_text('{"k": %d}' % (i + 1), encoding="utf-8")
                os.replace(tmp_path, files / f"f{i}.json")

        _timed(f"{N} small files created", create)
        _timed(f"{N} small files rewritten through os.replace", replace)
        _timed(f"{N} sqlite connections opened and closed (WAL file pairs)", lambda: [sqlite3.connect(base / f"c{i}.db").execute("PRAGMA journal_mode=WAL").fetchall() for i in range(N)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
