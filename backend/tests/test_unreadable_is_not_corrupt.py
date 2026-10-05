"""A file that cannot be read *right now* is not a corrupt file (a Windows data-loss path, reproduced on any OS).

On Windows, opening a file while a writer's ``os.replace`` has it can fail with ``PermissionError`` although the
file is healthy. Two stores treated every read failure as "corrupt": the smart-collections store and the literature
manifest renamed the healthy file aside (``*.corrupt``) and handed the caller an empty one — in CI the first of them
failed ``test_b3_concurrent_read_write_never_sees_torn_json``. Now a transient failure is retried, and one that
persists is raised rather than mistaken for corruption. Genuinely unparseable files are still quarantined.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import _fsutil
from app.services import literature_manifest as lm
from app.services import smart_collections as sc


class _Flaky:
    """``Path.read_text`` that raises ``PermissionError`` for the first *fail* calls on one file."""

    def __init__(self, target: Path, fail: int | None):
        self.target, self.fail, self.calls = target, fail, 0
        self._real = Path.read_text

    def __call__(self, path, *args, **kwargs):
        if Path(path) == self.target:
            self.calls += 1
            if self.fail is None or self.calls <= self.fail:
                raise PermissionError(13, "Access is denied")
        return self._real(path, *args, **kwargs)


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(_fsutil.time, "sleep", lambda s: None)


def test_a_transient_permission_error_is_retried_on_windows(tmp_path, monkeypatch, no_sleep):
    path = tmp_path / "x.json"
    path.write_text("[1]", encoding="utf-8")
    flaky = _Flaky(path, fail=3)
    monkeypatch.setattr(_fsutil.sys, "platform", "win32")
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: flaky(self, *a, **k))
    assert _fsutil.read_text_with_retry(path) == "[1]"
    assert flaky.calls == 4


def test_elsewhere_a_permission_error_is_raised_at_once(tmp_path, monkeypatch, no_sleep):
    path = tmp_path / "x.json"
    path.write_text("[1]", encoding="utf-8")
    flaky = _Flaky(path, fail=None)
    monkeypatch.setattr(_fsutil.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: flaky(self, *a, **k))
    with pytest.raises(PermissionError):
        _fsutil.read_text_with_retry(path)
    assert flaky.calls == 1


def test_a_permission_error_that_persists_is_raised_after_a_bounded_number_of_tries(tmp_path, monkeypatch, no_sleep):
    path = tmp_path / "x.json"
    path.write_text("[1]", encoding="utf-8")
    flaky = _Flaky(path, fail=None)
    monkeypatch.setattr(_fsutil.sys, "platform", "win32")
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: flaky(self, *a, **k))
    with pytest.raises(PermissionError):
        _fsutil.read_text_with_retry(path)
    assert flaky.calls == _fsutil._RETRIES


def test_smart_collections_does_not_quarantine_a_healthy_store_it_cannot_read(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_data_root", lambda: tmp_path)
    pid = "p-unreadable"
    sc.create_collection(pid, name="c", query="q")
    path = sc.collections_path(pid)
    before = path.read_text(encoding="utf-8")
    flaky = _Flaky(path, fail=None)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: flaky(self, *a, **k))
    with pytest.raises(PermissionError):
        sc.list_collections(pid)
    monkeypatch.undo()
    assert path.is_file(), "the store was moved aside although it is healthy"
    assert path.read_text(encoding="utf-8") == before
    assert not list(path.parent.glob("collections.json.corrupt-*"))


def test_smart_collections_still_quarantines_a_store_that_is_really_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_data_root", lambda: tmp_path)
    pid = "p-really-corrupt"
    sc.create_collection(pid, name="c", query="q")
    path = sc.collections_path(pid)
    path.write_text('{"collections": [', encoding="utf-8")
    assert sc.list_collections(pid) == []
    assert len(list(path.parent.glob("collections.json.corrupt-*"))) == 1


def test_the_literature_manifest_is_not_renamed_to_corrupt_when_it_cannot_be_read(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    pid = "p-manifest-unreadable"
    manifest = lm.empty_manifest(pid)
    manifest["items"] = [{"id": "1", "title": "Epoxy primer"}]
    lm.save_manifest(manifest)
    path = lm.manifest_path(pid)
    flaky = _Flaky(path, fail=None)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: flaky(self, *a, **k))
    with pytest.raises(PermissionError):
        lm.load_manifest(pid)
    monkeypatch.undo()
    assert path.is_file()
    assert not path.with_name(path.name + ".corrupt").exists()
    assert [i["id"] for i in json.loads(path.read_text(encoding="utf-8"))["items"]] == ["1"]


def test_the_literature_manifest_still_quarantines_an_unparseable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    pid = "p-manifest-corrupt"
    lm.save_manifest(lm.empty_manifest(pid))
    path = lm.manifest_path(pid)
    path.write_text("{not json", encoding="utf-8")
    assert lm.load_manifest(pid)["items"] == []
    assert path.with_name(path.name + ".corrupt").is_file()
