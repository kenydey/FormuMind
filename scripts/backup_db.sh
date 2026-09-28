#!/usr/bin/env bash
# One-shot SQLite backup with timestamp.
#
# Usage: scripts/backup_db.sh [db_path] [backup_dir] [filename_prefix]
#   db_path    default ./data/formumind.db
#   backup_dir default ./data/backups
#   filename_prefix default "formumind"
#
# Prints the created backup path. Uses the SQLite online backup API
# (consistent snapshot even while the server runs); falls back to cp when
# python3 is unavailable.
set -euo pipefail

DB="${1:-./data/formumind.db}"
BACKUP_DIR="${2:-./data/backups}"
PREFIX="${3:-formumind}"

if [ ! -f "$DB" ]; then
  echo "error: database not found: $DB" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/$PREFIX-$TS.db"

if command -v python3 >/dev/null 2>&1; then
  python3 - "$DB" "$DEST" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
EOF
else
  cp "$DB" "$DEST"
fi

echo "$DEST"
