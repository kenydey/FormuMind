#!/usr/bin/env bash
# Does the image we ship RUN?  (round-5)
#
# CI proved the Dockerfile's extras *resolve* (scripts/check_docker_extras.py, dry-run) and nothing ever built the image, so
# "installs" and "runs" were two different claims with only one of them checked. This builds it and then does what a user's
# first five minutes do: import the app, run the migrations, boot the server, read /health, and push a text file and a PDF
# through the ingest endpoint - with no volume mounted, no Redis, no Datalab, because that is the out-of-the-box case.
#
#   scripts/docker_smoke.sh                 # build + every check
#   SKIP_BUILD=1 IMAGE=my:tag scripts/docker_smoke.sh
#
# Environment: IMAGE (tag to build/run), CONTEXT (build context), PORT (host port), BOOT_TIMEOUT / TASK_TIMEOUT /
# HEALTHY_TIMEOUT (seconds), SKIP_BUILD=1 (use an existing image), KEEP=1 (leave the container running).
# Exit status is 0 only when every check passed; on a failure the container's log tail is printed first.
set -euo pipefail

IMAGE="${IMAGE:-formumind-backend:smoke}"
CONTEXT="${CONTEXT:-backend}"
NAME="${NAME:-formumind-smoke-$$}"
PORT="${PORT:-18000}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-240}"
TASK_TIMEOUT="${TASK_TIMEOUT:-180}"
HEALTHY_TIMEOUT="${HEALTHY_TIMEOUT:-120}"
SKIP_BUILD="${SKIP_BUILD:-0}"
KEEP="${KEEP:-0}"
BASE="http://127.0.0.1:${PORT}"
WORK="$(mktemp -d)"
FAILED=0

step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '    PASS  %s\n' "$*"; }
bad()  { printf '    FAIL  %s\n' "$*" >&2; FAILED=1; }

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ] || [ "$FAILED" -ne 0 ]; then
    if docker inspect "$NAME" >/dev/null 2>&1; then
      printf '\n---- container log (tail) ----\n' >&2
      docker logs --tail 120 "$NAME" >&2 2>&1 || true
    fi
  fi
  if [ "$KEEP" != "1" ]; then
    docker rm -f "$NAME" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# wait_for <seconds> <description> <command...>  - run the command every 2 s until it succeeds or the time is up
wait_for() {
  local limit="$1" what="$2"
  shift 2
  local waited=0
  until "$@" >/dev/null 2>&1; do
    waited=$((waited + 2))
    if [ "$waited" -ge "$limit" ]; then
      bad "$what did not happen within ${limit}s"
      return 1
    fi
    sleep 2
  done
  ok "$what (${waited}s)"
}

# json <expression over the parsed document d>  - reads JSON on stdin and prints the value
json() { python3 -c 'import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

health_database_ok() { curl -fsS --max-time 5 "$BASE/health" | json 'd["database"]["ok"]' | grep -qx True; }

container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]; }

container_healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null)" = "healthy" ]; }

# upload <file> <mime>  -> prints the task id of the accepted background job
upload() {
  curl -fsS --max-time 60 -F "file=@$1;type=$2" "$BASE/api/ingest" | json 'd["task_id"]'
}

task_state() { curl -fsS --max-time 10 "$BASE/api/tasks/$1" | json 'd["state"]'; }

task_total() { curl -fsS --max-time 10 "$BASE/api/tasks/$1" | json '(d.get("result") or {}).get("total", 0)'; }

task_ended() { case "$(task_state "$1")" in completed | failed) return 0 ;; *) return 1 ;; esac; }

# ingest_ok <label> <file> <mime>  - the file must go 202 -> completed, with evidence in the result
ingest_ok() {
  local label="$1" file="$2" mime="$3" task state total
  if ! task="$(upload "$file" "$mime")"; then
    bad "$label: the upload was not accepted"
    return 1
  fi
  if ! wait_for "$TASK_TIMEOUT" "$label: the task ended" task_ended "$task"; then
    return 1
  fi
  state="$(task_state "$task")"
  total="$(task_total "$task")"
  if [ "$state" = "completed" ] && [ "${total:-0}" -ge 1 ]; then
    ok "$label: completed with $total evidence item(s)"
  else
    bad "$label: state=$state, evidence=${total:-0}"
    return 1
  fi
}

# ---------------------------------------------------------------- build

if [ "$SKIP_BUILD" = "1" ]; then
  step "using the existing image $IMAGE"
else
  step "build $IMAGE from $CONTEXT"
  docker build -t "$IMAGE" "$CONTEXT"
  ok "the image builds"
fi

# ---------------------------------------------------------------- the image on its own

step "the application imports"
docker run --rm "$IMAGE" python -c "import app.main; print('app.main imports')"
ok "app.main imports"

step "the image can parse what it advertises (no optional parser is installed at run time)"
PARSERS="$(docker run --rm "$IMAGE" python -c "import json; from app.services.parsing import format_availability; print(json.dumps(format_availability()))")"
echo "    $PARSERS"
if printf '%s' "$PARSERS" | json 'bool(d.get("pdf"))' | grep -qx True; then
  ok "a PDF parser is installed"
else
  bad "no PDF parser in the image: uploads would be accepted and nothing indexed"
fi

step "migrations run inside the image"
docker run --rm -e FORMUMIND_DB_URL=sqlite:////tmp/smoke.db "$IMAGE" alembic upgrade head
ok "alembic upgrade head"

# ---------------------------------------------------------------- the server

# No volume, no Redis, no Datalab: tasks run in-process (eager) and the ledger is the local sqlite one. The data directory
# is the image's own - `docker run` without a mount used to stop at startup because nothing created it.
step "boot the server ($NAME on :$PORT)"
docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8000" \
  -e FORMUMIND_API_AUTH_ENABLED=false \
  -e FORMUMIND_CELERY_EAGER=true \
  -e FORMUMIND_CAMPAIGN_BACKEND=sqlite \
  -e FORMUMIND_EXPERIMENT_BACKEND=sqlite \
  -e FORMUMIND_DATALAB_REQUIRED=false \
  -e FORMUMIND_NEO4J_ENABLED=false \
  "$IMAGE" >/dev/null
wait_for "$BOOT_TIMEOUT" "/health answers with the database ok" health_database_ok || true
if container_running; then ok "the container is still running"; else bad "the container exited"; fi

if [ "$FAILED" -eq 0 ]; then
  step "ingest through the running image"
  printf '%s\n' \
    'Zinc phosphate is added to a waterborne epoxy primer as an anticorrosive pigment at 6 to 10 percent by weight.' \
    'Neutral salt spray resistance of the cured film is reported after 500 hours according to ASTM B117.' \
    'Adhesion to cold-rolled steel is tested by cross-cut after a 72 hour water immersion.' >"$WORK/sample.txt"
  ingest_ok "text file" "$WORK/sample.txt" "text/plain" || true

  # The PDF is written by the image's own PyMuPDF, so the check needs nothing the image does not ship.
  docker exec "$NAME" python -c "
import fitz
doc = fitz.open()
page = doc.new_page()
page.insert_textbox(fitz.Rect(72, 72, 520, 400),
    'Zinc phosphate is added to a waterborne epoxy primer as an anticorrosive pigment at six to ten percent by weight. '
    'Neutral salt spray resistance of the cured film is reported after five hundred hours according to ASTM B117. '
    'Adhesion to cold rolled steel is tested by cross cut after a seventy two hour water immersion.', fontsize=11)
doc.save('/tmp/smoke.pdf')
" && docker cp "$NAME:/tmp/smoke.pdf" "$WORK/smoke.pdf"
  if [ -s "$WORK/smoke.pdf" ]; then
    ingest_ok "PDF" "$WORK/smoke.pdf" "application/pdf" || true
  else
    bad "PDF: could not create the test document inside the image"
  fi

  step "the image's own HEALTHCHECK"
  wait_for "$HEALTHY_TIMEOUT" "docker reports the container healthy" container_healthy || true
fi

# ---------------------------------------------------------------- verdict

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "Some checks failed." >&2
exit 1
