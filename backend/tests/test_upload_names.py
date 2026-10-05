"""What an uploaded file is called on disk must not decide what gets ingested (round-4).

``_write_upload`` kept the client's basename and wrote straight into one shared directory, so
* two parts with the same name — a folder upload with ``datasheet.pdf`` in several sub-folders — landed on the same path:
  the first was silently replaced by the second, which was then ingested twice (reproduced end to end below);
* names that are legal on a macOS / Linux client but not on Windows (``TDS: epoxy primer.pdf``, ``what?.docx``) raised
  from ``open()`` — a 500 on a Windows server — and device names (``CON``, ``NUL.txt``) are not files there;
* ``os.path.basename`` only knows the separators of the server's own OS, so a client's ``..\\..\\x`` was a literal name
  on Linux and a traversal attempt only on Windows.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import ingest as ingest_api
from app.main import app


@pytest.mark.parametrize(
    "name, expected",
    [
        ("datasheet.pdf", "datasheet.pdf"),
        ("TDS: epoxy primer.pdf", "TDS_ epoxy primer.pdf"),
        ("what?.docx", "what_.docx"),
        ("a<b>c|d*e\"f.txt", "a_b_c_d_e_f.txt"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system.ini", "system.ini"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        ("/abs/path/report.pdf", "report.pdf"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("COM1.pdf", "_COM1.pdf"),
        ("report.pdf  ", "report.pdf"),
        ("...", "upload"),
        ("", "upload"),
        ("\x00\x01.txt", "__.txt"),
    ],
)
def test_the_disk_name_is_valid_everywhere_and_cannot_traverse(name, expected):
    assert ingest_api._disk_name(name) == expected


def test_a_very_long_name_is_cut_but_keeps_its_extension():
    name = ingest_api._disk_name("x" * 400 + ".pdf")
    assert name.endswith(".pdf") and len(name) <= 120


def test_parts_with_the_same_name_do_not_overwrite_each_other(tmp_path):
    first = ingest_api._write_upload(b"first", "datasheet.pdf", str(tmp_path))
    second = ingest_api._write_upload(b"second", "datasheet.pdf", str(tmp_path))
    assert first != second
    assert Path(first).read_bytes() == b"first" and Path(second).read_bytes() == b"second"
    assert Path(first).name == Path(second).name == "datasheet.pdf", "the file keeps its name inside its own directory"


def test_nothing_is_written_outside_the_upload_directory(tmp_path):
    inside = tmp_path / "up"
    inside.mkdir()
    for name in ("../../escape.txt", "..\\..\\escape.txt", "/tmp/escape.txt", "C:\\escape.txt"):
        written = Path(ingest_api._write_upload(b"x", name, str(inside))).resolve()
        assert inside.resolve() in written.parents, (name, written)
    assert not (tmp_path / "escape.txt").exists()


def test_a_batch_with_two_files_of_the_same_name_ingests_both():
    """End to end through ``POST /api/ingest/batch``: before the fix both evidence rows held the second file's text."""
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    first = f"FIRST file: epoxy zinc primer data {tag} salt spray 720 h."
    second = f"SECOND file: polyurethane topcoat data {tag} gloss 90 GU."
    r = client.post(
        "/api/ingest/batch",
        files=[
            ("files", ("datasheet.txt", first.encode(), "text/plain")),
            ("files", ("datasheet.txt", second.encode(), "text/plain")),
        ],
    )
    assert r.status_code == 202, r.text
    deadline = time.monotonic() + 60
    task = {}
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{r.json()['task_id']}").json()
        if task.get("state") in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert task.get("state") == "completed", task
    snippets = " ".join(e["snippet"] for e in (task.get("result") or {}).get("evidence", []))
    assert "FIRST file" in snippets and "SECOND file" in snippets, snippets


def test_a_windows_hostile_file_name_is_accepted_by_the_endpoint():
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    r = client.post(
        "/api/ingest",
        files={"file": ("TDS: epoxy primer?.txt", f"epoxy primer technical data {tag} solids 65 %.".encode(), "text/plain")},
    )
    assert r.status_code == 202, r.text
