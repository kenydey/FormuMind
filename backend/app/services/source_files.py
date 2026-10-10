"""Source file persistence for "open in new page" (PageIndex 借鉴 P6).

Uploaded originals are persisted under ``data/source_files/{source_id}{ext}``
so the download endpoint can serve them. Fail-open everywhere: persistence
never blocks ingestion.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def source_files_dir() -> Path:
    # data/ 与 DB 同目录（backend/data/，Docker 下 volume-mounted）。
    backend_dir = Path(__file__).resolve().parents[2]
    d = backend_dir / "data" / "source_files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def persist_source_file(source_id: str, src_path: Path, filename: str) -> Path | None:
    """Copy an uploaded file to persistent storage. Returns the dest path."""
    try:
        ext = os.path.splitext(filename or "")[1][:10] or ".bin"
        # Sanitize: keep alnum + dot only.
        ext = "".join(c for c in ext if c.isalnum() or c == ".") or ".bin"
        dest = source_files_dir() / f"{source_id}{ext}"
        if dest.exists():
            return dest
        shutil.copy2(src_path, dest)
        logger.info("source file persisted: %s → %s", filename, dest.name)
        return dest
    except Exception as exc:  # noqa: BLE001
        logger.debug("persist_source_file failed for %s: %s", filename, exc)
        return None


def find_source_file(source_id: str) -> Path | None:
    """Locate a persisted source file by source_id (any extension)."""
    try:
        d = source_files_dir()
        # Exact stem match; extension varies.
        for p in d.iterdir():
            if p.is_file() and p.stem == source_id:
                return p
        return None
    except Exception:
        return None
