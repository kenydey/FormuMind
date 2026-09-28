"""B-17 回归：上传先全量读入内存再检查大小 → 分块读、超限即停并返回 413。
验证 _read_upload_capped：
1. 正常文件完整读回；
2. 超限文件在累计超限的当块即抛 413，不再读后续分块（内存有界）；
3. 恰好等于上限的文件通过（total > limit 才触发）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import ingest as ingest_mod


class _FakeUpload:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.reads = 0

    async def read(self, size=-1):
        self.reads += 1
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


@pytest.fixture()
def cap_2mib(monkeypatch):
    monkeypatch.setattr(
        ingest_mod,
        "get_settings",
        lambda: SimpleNamespace(ingest_max_upload_bytes=2 * 1024 * 1024),
    )


def test_under_limit_reads_fully(cap_2mib):
    up = _FakeUpload([b"a" * (1024 * 1024), b"b" * (512 * 1024)])
    content = asyncio.run(ingest_mod._read_upload_capped(up, "ok.pdf"))
    assert content == b"a" * (1024 * 1024) + b"b" * (512 * 1024)


def test_over_limit_stops_early_with_413(cap_2mib):
    # 5 个 1MiB 分块，上限 2MiB：第 3 块累计 3MiB 即停，
    # 不应把 5MiB 全读进内存
    up = _FakeUpload([b"x" * (1024 * 1024)] * 5)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ingest_mod._read_upload_capped(up, "big.pdf"))
    assert ei.value.status_code == 413
    assert up.reads == 3, f"应提前停止，实际读了 {up.reads} 块"


def test_exact_limit_passes(cap_2mib):
    up = _FakeUpload([b"y" * (1024 * 1024), b"y" * (1024 * 1024)])
    content = asyncio.run(ingest_mod._read_upload_capped(up, "edge.pdf"))
    assert len(content) == 2 * 1024 * 1024
