"""Shared multipart-upload reading with a hard size cap (B-17).

``await file.read()`` pulls the whole part into memory before any size check can
run. Endpoints that did that (and most checked only afterwards, some never)
let one oversized upload exhaust the worker. :func:`read_upload_capped` reads in
1 MiB chunks and answers HTTP 413 as soon as the running total passes the cap.
"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

from ..config import get_settings

_CHUNK_BYTES = 1024 * 1024


def upload_limit_bytes() -> int:
    """Per-file cap from Settings (``ingest_max_upload_bytes``, default 20 MiB)."""
    return int(get_settings().ingest_max_upload_bytes)


def too_large(filename: str, limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"File {filename!r} exceeds upload limit ({limit // (1024 * 1024)} MiB)",
    )


async def read_upload_capped(file: UploadFile, filename: str, *, limit: int | None = None) -> bytes:
    """Read *file* fully, raising 413 the moment more than *limit* bytes arrive."""
    cap = upload_limit_bytes() if limit is None else int(limit)
    size = getattr(file, "size", None)
    if size is not None and size > cap:
        raise too_large(filename, cap)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise too_large(filename, cap)
        chunks.append(chunk)
    return b"".join(chunks)
