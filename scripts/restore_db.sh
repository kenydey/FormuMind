#!/usr/bin/env bash
# Restore a SQLite backup.
#
# Usage: scripts/restore_db.sh <backup_file> [db_path]
#   db_path default ./data/formumind.db
#
# Safety: verifies the backup with PRAGMA integrity_check, then auto-backs up
# the CURRENT database (pre-restore-<timestamp>.db) before overwriting.
# Stop the backend/worker before restoring (the DB file is replaced).
set -euo pipefail

BACKUP="${1:?usage: scripts/restore_db.sh <backup_file> [db_path]}"
DB="${2:-./data/formumind.db}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$BACKUP" ]; then
  echo "error: backup not found: $BACKUP" >&2
  exit 1
fi

echo "Verifying backup integrity..."
if command -v sqlite3 >/dev/null 2>&1; then
  RESULT="$(sqlite3 "$BACKUP" "PRAGMA integrity_check;")"
  if [ "$RESULT" != "ok" ]; then
    echo "error: backup failed integrity_check: $RESULT" >&2
    exit 1
  fi
fi

if [ -f "$DB" ]; then
  # Distinct prefix avoids colliding with the backup being restored when both
  # live in the same directory.
  SAFE="$(bash "$SCRIPT_DIR/backup_db.sh" "$DB" "$(dirname "$DB")/backups" "pre-restore")"
  echo "Current DB safety copy: $SAFE"
fi

cp "$BACKUP" "$DB"
echo "Restored $BACKUP -> $DB"
