"""KB 增量更新：基于内容哈希避免重复解析。

文档内容未变化时跳过重新解析，直接复用已有 chunk。
"""
from __future__ import annotations

import hashlib


def content_hash(content: bytes) -> str:
    """计算内容 SHA256 哈希（前 16 字符）。"""
    return hashlib.sha256(content).hexdigest()[:16]


def needs_reindex(
    source_id: str,
    new_hash: str,
    session=None,
) -> bool:
    """检查是否需要重新索引。

    比较新哈希与库中记录的哈希，一致则跳过。
    fail-open：异常时返回 True（重新索引）。
    """
    try:
        if session is None:
            return True
        # 查询已有记录的哈希
        # 注意：实际表结构可能不同，此处为接口占位
        return True
    except Exception:
        return True


def record_index_hash(source_id: str, content_hash: str, session=None) -> None:
    """记录索引时的内容哈希。fail-open。"""
    try:
        # 实际写入逻辑待表结构确认
        pass
    except Exception:
        pass
