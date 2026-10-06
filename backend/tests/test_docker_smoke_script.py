"""``scripts/docker_smoke.sh`` against a fake ``docker`` and a fake FormuMind server (round-5).

The real run is the ``docker-smoke`` workflow: it builds the image (tens of minutes, several GB) and cannot be part of the
unit suite. What can be tested here is the script's own logic - that it fails when the image does not run, instead of
reporting a green smoke test for a container that exited, an API that answers 503, a task that failed or an image that cannot
parse a PDF. ``docker`` is a shim that records its arguments and answers from environment variables; the server is a few
lines of ``http.server`` whose answers the test chooses.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "docker_smoke.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or sys.platform == "win32" or shutil.which("curl") is None,
    reason="needs bash and curl (the Linux jobs have both)",
)

DOCKER_SHIM = r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  build) exit "${FAKE_BUILD_RC:-0}" ;;
  run)
    case " $* " in
      *" -d "*) echo fake-container-id; exit 0 ;;
    esac
    case "$*" in
      *format_availability*) echo "${FAKE_PARSERS:-{\"pdf\": [\"pymupdf4llm\"], \"docx\": [\"python-docx\"]\}}"; exit 0 ;;
      *"import app.main"*) echo "app.main imports"; exit "${FAKE_IMPORT_RC:-0}" ;;
    esac
    exit 0 ;;
  exec) exit 0 ;;
  cp) printf '%%PDF-1.4 fake' > "${@: -1}"; exit 0 ;;
  inspect)
    case "$*" in
      *State.Running*) echo "${FAKE_RUNNING:-true}" ;;
      *State.Health.Status*) echo "${FAKE_HEALTH:-healthy}" ;;
    esac
    exit 0 ;;
  logs) echo "FAKE-CONTAINER-LOG-LINE"; exit 0 ;;
  rm) exit 0 ;;
esac
exit 0
"""


class _App(BaseHTTPRequestHandler):
    """What the script asks of FormuMind: /health, POST /api/ingest, GET /api/tasks/<id>."""

    health_status = 200
    task_state = "completed"
    task_total = 3
    seen: list[str] = []

    def log_message(self, *args):  # silence the test output
        pass

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _App.seen.append(f"GET {self.path}")
        if self.path == "/health":
            self._send(_App.health_status, {"status": "ok", "database": {"ok": True}})
        elif self.path.startswith("/api/tasks/"):
            self._send(200, {"state": _App.task_state, "result": {"total": _App.task_total}})
        else:
            self._send(404, {})

    def do_POST(self):
        _App.seen.append(f"POST {self.path}")
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(202, {"task_id": "t-1"})


@pytest.fixture()
def server():
    _App.health_status, _App.task_state, _App.task_total, _App.seen = 200, "completed", 3, []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _App)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def run_smoke(tmp_path, server):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "docker"
    shim.write_text(DOCKER_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    log = tmp_path / "docker.log"

    def run(**env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        result = subprocess.run(
            [BASH, str(SCRIPT)],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(log),
                 "PORT": str(server), "BOOT_TIMEOUT": "2", "TASK_TIMEOUT": "2", "HEALTHY_TIMEOUT": "2", **env},
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    return run


def test_a_healthy_image_passes_every_check(run_smoke):
    result, calls = run_smoke()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed." in result.stdout
    assert any(c.startswith("docker build -t formumind-backend:smoke backend") for c in calls)
    assert any(c.startswith("docker run -d") and "-p 127.0.0.1:" in c for c in calls)
    assert any(c.startswith("docker rm -f") for c in calls), "the container must be removed afterwards"
    assert result.stdout.count("completed with 3 evidence item(s)") == 2, "both the text file and the PDF are ingested"


def test_the_container_boots_the_way_a_first_run_does(run_smoke):
    """No volume, no Redis, no Datalab: tasks run in-process and the ledger is the local one."""
    _, calls = run_smoke()
    boot = next(c for c in calls if c.startswith("docker run -d"))
    for setting in ("FORMUMIND_CELERY_EAGER=true", "FORMUMIND_DATALAB_REQUIRED=false", "FORMUMIND_API_AUTH_ENABLED=false"):
        assert setting in boot
    assert " -v " not in boot and "--volume" not in boot, "a mounted data directory would hide the missing-directory failure"


def test_an_api_that_never_answers_fails_and_shows_the_container_log(run_smoke):
    _App.health_status = 503
    result, calls = run_smoke()
    assert result.returncode == 1
    assert "did not happen within 2s" in result.stderr
    assert "FAKE-CONTAINER-LOG-LINE" in result.stderr, "the log tail is the first thing wanted after a failure"
    assert any(c.startswith("docker rm -f") for c in calls)


def test_an_exited_container_fails(run_smoke):
    result, _ = run_smoke(FAKE_RUNNING="false")
    assert result.returncode == 1 and "the container exited" in result.stderr


def test_a_failed_ingest_task_fails(run_smoke):
    _App.task_state = "failed"
    result, _ = run_smoke()
    assert result.returncode == 1
    assert "state=failed" in result.stderr


def test_a_task_that_completes_with_nothing_parsed_fails(run_smoke):
    _App.task_total = 0
    result, _ = run_smoke()
    assert result.returncode == 1 and "evidence=0" in result.stderr


def test_an_image_without_a_pdf_parser_fails(run_smoke):
    result, _ = run_smoke(FAKE_PARSERS=json.dumps({"pdf": [], "docx": ["python-docx"]}))
    assert result.returncode == 1
    assert "no PDF parser in the image" in result.stderr


def test_an_unhealthy_container_fails(run_smoke):
    result, _ = run_smoke(FAKE_HEALTH="unhealthy")
    assert result.returncode == 1 and "docker reports the container healthy did not happen" in result.stderr


def test_a_build_failure_stops_before_anything_runs(run_smoke):
    result, calls = run_smoke(FAKE_BUILD_RC="1")
    assert result.returncode != 0
    assert not any(c.startswith("docker run") for c in calls)


def test_an_image_that_does_not_import_stops_the_run(run_smoke):
    result, calls = run_smoke(FAKE_IMPORT_RC="1")
    assert result.returncode != 0
    assert not any(c.startswith("docker run -d") for c in calls), "it must not go on to boot a server"


def test_skip_build_uses_the_image_it_is_given(run_smoke):
    result, calls = run_smoke(SKIP_BUILD="1", IMAGE="already:built")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(c.startswith("docker build") for c in calls)
    assert any("already:built" in c for c in calls if c.startswith("docker run"))


def test_keep_leaves_the_container_for_inspection(run_smoke):
    _, calls = run_smoke(KEEP="1")
    assert not any(c.startswith("docker rm -f") for c in calls)


def test_the_dockerfile_creates_the_data_directory_the_startup_check_requires():
    """The check refuses to boot without it; compose hides the gap by mounting ./data. An unmounted `docker run` needs it."""
    dockerfile = (REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "mkdir -p /app/data" in dockerfile
    assert dockerfile.index("mkdir -p /app/data") < dockerfile.index("CMD [")
