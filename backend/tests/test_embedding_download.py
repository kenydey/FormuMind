"""The cache probe and the download task for embedding models."""
from __future__ import annotations

import pytest

from app.services import embedding_download as ed

MODEL = "BAAI/bge-m3"


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HOME", raising=False)
    return tmp_path


def _snapshot(hub, *files):
    snap = hub / "models--BAAI--bge-m3" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    for name in files:
        (snap / name).write_bytes(b"x")
    return snap


def test_an_empty_model_directory_left_by_an_interrupted_download_is_not_cached(hub):
    (hub / "models--BAAI--bge-m3" / "blobs").mkdir(parents=True)
    assert ed.is_model_cached(MODEL) is False


def test_a_snapshot_without_weights_is_not_cached(hub):
    _snapshot(hub, "config.json")
    assert ed.is_model_cached(MODEL) is False


def test_a_snapshot_with_config_and_weights_is_cached(hub):
    _snapshot(hub, "config.json", "model.safetensors")
    assert ed.is_model_cached(MODEL) is True


def test_the_hub_cache_follows_hf_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    snap = tmp_path / "hub" / "models--BAAI--bge-m3" / "snapshots" / "r"
    snap.mkdir(parents=True)
    (snap / "config.json").write_bytes(b"{}")
    (snap / "pytorch_model.bin").write_bytes(b"x")
    assert ed.is_model_cached(MODEL) is True


def test_starting_a_download_of_a_cached_model_is_done_at_once_without_a_thread(hub, monkeypatch):
    _snapshot(hub, "config.json", "model.safetensors")

    def boom(*_a, **_kw):
        raise AssertionError("a cached model must not start a download worker")

    monkeypatch.setattr(ed.threading, "Thread", boom)
    task = ed.get_task(ed.start_download(MODEL))
    assert (task.status, task.progress) == ("done", 100.0)
