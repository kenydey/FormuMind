"""知识库元数据丰富化：chunk 自动标签。

基于内容关键词自动打标签，便于过滤和检索。
全部 fail-open，不阻塞入库。
"""
from __future__ import annotations

# 标签 → 关键词（大小写不敏感）
_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "配方": ("配方", "formulation", "recipe", "配比", "组成"),
    "专利": ("专利", "patent", "claim", "权利要求", "实施例"),
    "安全": ("安全", "safety", "MSDS", "危害", "hazard", "毒性"),
    "工艺": ("工艺", "process", "固化", "curing", "干燥", "drying"),
    "测试": ("测试", "test", "检测", "标准", "ASTM", "ISO", "GB"),
    "成分": ("成分", "component", "ingredient", "原料", "material"),
    "性能": ("性能", "performance", "硬度", "hardness", "附着力", "adhesion"),
}


def auto_tag_chunk(text: str) -> list[str]:
    """为 chunk 文本自动打标签。返回标签列表。"""
    if not text:
        return []
    lowered = text.lower()
    tags = []
    for tag, keywords in _TAG_KEYWORDS.items():
        if any(kw.lower() in lowered for kw in keywords):
            tags.append(tag)
    return tags


def enrich_chunk_metadata(rows: list[dict]) -> list[dict]:
    """为 chunk rows 添加 auto_tags 元数据。fail-open。"""
    try:
        for row in rows:
            text = row.get("text", "")
            tags = auto_tag_chunk(text)
            if tags:
                # 合并到现有元数据
                meta = row.get("metadata") or {}
                existing = meta.get("auto_tags", [])
                meta["auto_tags"] = list(set(existing + tags))
                row["metadata"] = meta
    except Exception:
        pass
    return rows
