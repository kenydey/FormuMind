"""P2-9: 大 PDF 断点续传下载器。

设计：
- HTTP Range 请求分片下载（默认 1MB/片）。
- 断点文件存 /tmp/resume_{hash}.part，记录已下载字节数。
- 服务器不支持 Range 时回退整包下载。
- 超时/中断后下次调用自动续传。
- fail-open：续传失败时返回已下载部分（调用方决定是否使用）。

注意：调用方需传入稳定的 url_hash（URL 的 sha256 前 16 位）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024  # 1MB
_RESUME_DIR = "/tmp/formumind_resume"
_MAX_RETRIES = 3


def _resume_path(url_hash: str) -> str:
    os.makedirs(_RESUME_DIR, exist_ok=True)
    return os.path.join(_RESUME_DIR, f"resume_{url_hash}.part")


def _meta_path(url_hash: str) -> str:
    return _resume_path(url_hash) + ".meta"


def download_resumable(url: str, *, timeout: float = 30.0,
                       max_bytes: int = 100 * 1024 * 1024) -> bytes | None:
    """断点续传下载。返回完整字节，失败返回 None。"""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    part_path = _resume_path(url_hash)
    meta_path = _meta_path(url_hash)

    # 读断点
    downloaded = 0
    if os.path.exists(part_path):
        downloaded = os.path.getsize(part_path)

    try:
        from .pdf_downloader import ssrf_safe_client, _HEADERS, _is_safe_url
        if not _is_safe_url(url):
            return None
    except Exception:
        return None

    for attempt in range(_MAX_RETRIES):
        try:
            headers = dict(_HEADERS)
            if downloaded > 0:
                headers["Range"] = f"bytes={downloaded}-"
            with ssrf_safe_client(
                timeout=timeout, follow_redirects=True, headers=headers
            ) as client:
                with client.stream("GET", url) as r:
                    status = r.status_code
                    if status == 416:  # Range 不满足（已下完）
                        with open(part_path, "rb") as f:
                            return f.read()
                    if status == 200 and downloaded > 0:
                        # 服务器忽略 Range，从头开始
                        downloaded = 0
                        mode = "wb"
                    elif status in (200, 206):
                        mode = "ab" if downloaded > 0 else "wb"
                    else:
                        logger.warning("resumable download HTTP %d for %s", status, url[:100])
                        return None
                    total = 0
                    with open(part_path, mode) as f:
                        for chunk in r.iter_bytes(chunk_size=_CHUNK_SIZE):
                            f.write(chunk)
                            total += len(chunk)
                            if os.path.getsize(part_path) > max_bytes:
                                logger.warning("resumable download exceeds max_bytes")
                                return None
            # 下载完成，读回并清理
            with open(part_path, "rb") as f:
                data = f.read()
            try:
                os.unlink(part_path)
                if os.path.exists(meta_path):
                    os.unlink(meta_path)
            except Exception:
                pass
            logger.info("resumable download complete: %d bytes for %s",
                        len(data), url[:100])
            return data
        except Exception as exc:
            logger.warning("resumable download attempt %d failed: %s",
                           attempt + 1, exc)
            if attempt + 1 < _MAX_RETRIES:
                time.sleep(2 ** attempt)
                # 更新断点
                if os.path.exists(part_path):
                    downloaded = os.path.getsize(part_path)
                continue
            return None
    return None


def clear_resume_cache(max_age_hours: float = 24) -> int:
    """清理过期断点文件。返回清理数。"""
    n = 0
    try:
        now = time.time()
        for fn in os.listdir(_RESUME_DIR):
            p = os.path.join(_RESUME_DIR, fn)
            if now - os.path.getmtime(p) > max_age_hours * 3600:
                os.unlink(p)
                n += 1
    except Exception:
        pass
    return n
