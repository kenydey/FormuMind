"""A finished task must not lose its result to a status poll that raced the writers (a Windows-only data loss, found by CI).

``run_file_ingest_task`` writes the terminal snapshot first and the result store just after. ``GET /api/tasks/<id>``
(``TaskManager.get``) prefers the disk snapshot — but when it could not *read* it (on Windows, a reader that opens a file
mid-``os.replace`` gets ``PermissionError``) it recomputed the status from the progress meta: "completed", result ``None``
because the result store was not written yet — and persisted that over the good snapshot. The task had finished and
its result was gone; ``test_ingest_text_file`` saw ``{}`` where it expected the ingested evidence.

Fixed three ways, each tested here: the recomputed status takes the result from the terminal event that carries it; the
status/progress files are written atomically (no torn reads, no shared temp file); and a transient read failure is retried.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from app.domain.schemas import TaskState
from app.services import _fsutil
from app.worker import task_progress, tasks


@pytest.fixture()
def task_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path))
    monkeypatch.setenv("FORMUMIND_TASK_PROGRESS_DIR", str(tmp_path / "progress"))
    return tmp_path


RESULT = {"total": 3, "evidence": [{"source": "local"}], "files_processed": 1}


def test_a_poll_that_cannot_read_the_snapshot_still_reports_the_result(task_dir, monkeypatch):
    task_id = "race-task"
    tasks._persist_terminal(task_id, "file_ingest", RESULT)  # snapshot + COMPLETED event; result store not written yet
    assert task_progress.get_task_result(task_id) is None

    real = tasks.load_persisted_task
    calls = {"n": 0}

    def unreadable_once(tid):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(tid)

    monkeypatch.setattr(tasks, "load_persisted_task", unreadable_once)
    status = tasks.task_manager.get(task_id)
    assert status.state == TaskState.completed
    assert status.result == RESULT, "the poll reported a finished task with no result"
    monkeypatch.setattr(tasks, "load_persisted_task", real)
    persisted = tasks.load_persisted_task(task_id)
    assert persisted.state == TaskState.completed and persisted.result == RESULT, (
        "the poll persisted a result-less snapshot over the good terminal one"
    )


def test_a_failed_task_keeps_its_error_the_same_way(task_dir, monkeypatch):
    task_id = "race-failed"
    tasks._persist_terminal(task_id, "file_ingest", {"error": "boom"}, failed=True, message="boom")
    real = tasks.load_persisted_task
    monkeypatch.setattr(tasks, "load_persisted_task", lambda tid: None)
    status = tasks.task_manager.get(task_id)
    assert status.state == TaskState.failed and status.result == {"error": "boom"}
    monkeypatch.setattr(tasks, "load_persisted_task", real)


def test_the_result_store_still_wins_when_it_has_the_result(task_dir):
    task_id = "race-store"
    tasks._persist_terminal(task_id, "file_ingest", {"total": 1})
    task_progress.persist_result(task_id, RESULT)
    meta = task_progress.get_task_meta(task_id)
    assert tasks._status_from_progress(task_id, "file_ingest").result == RESULT
    assert meta["status"] == "COMPLETED"


def test_a_running_task_is_not_given_a_result_from_nowhere(task_dir):
    task_id = "race-running"
    task_progress.publish_progress(task_id, task_progress.TaskProgressStatus.RUNNING, message="working", data={"x": 1})
    assert tasks._status_from_progress(task_id, "k").result is None


def test_atomic_write_leaves_only_the_target(tmp_path):
    target = tmp_path / "state.json"
    _fsutil.atomic_write_text(target, '{"a": 1}')
    _fsutil.atomic_write_text(target, '{"a": 2}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_a_failed_atomic_write_cleans_up_and_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def broken(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(_fsutil, "replace_with_retry", broken)
    with pytest.raises(PermissionError):
        _fsutil.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_concurrent_writers_never_show_a_reader_a_torn_file(tmp_path):
    target = tmp_path / "meta.json"
    payloads = [json.dumps({"writer": i, "pad": "x" * 200_000}) for i in range(4)]
    _fsutil.atomic_write_text(target, payloads[0])
    stop = threading.Event()
    torn: list[str] = []

    def writer(i: int) -> None:
        while not stop.is_set():
            _fsutil.atomic_write_text(target, payloads[i])

    def reader() -> None:
        while not stop.is_set():
            try:
                json.loads(_fsutil.read_text_with_retry(target))
            except ValueError as exc:  # a half-written file
                torn.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join()
    assert not torn, torn[:3]
    assert sorted(p.name for p in Path(tmp_path).iterdir()) == ["meta.json"]


def test_the_progress_files_are_written_through_the_atomic_helper(task_dir, monkeypatch):
    seen: list[str] = []
    real = _fsutil.replace_with_retry

    def spy(src, dst):
        seen.append(Path(dst).name)
        return real(src, dst)

    monkeypatch.setattr(_fsutil, "replace_with_retry", spy)
    task_progress._file_write_meta(
        "atomic-1", task_progress.TaskProgressEvent(status=task_progress.TaskProgressStatus.RUNNING), kind="k"
    )
    task_progress._file_write_result("atomic-1", {"a": 1})
    tasks._persist_task("atomic-1", tasks.TaskStatus(task_id="atomic-1", kind="k", state=TaskState.pending))
    assert sorted(seen) == ["atomic-1.json", "atomic-1.meta.json", "atomic-1.result.json"]
