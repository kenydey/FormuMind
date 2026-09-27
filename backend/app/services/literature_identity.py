"""文献身份归一与精确去重（Wave 1 · P0-6）。

跨数据源（OpenAlex / arXiv / DOI / Scholar…）同一文献常以不同写法出现，
例如 ``10.1016/X`` 与 ``https://doi.org/10.1016/X``、``arXiv:2101.0001v2`` 与
``2101.0001``。本模块提供：

* 归一化器：``normalize_doi`` / ``normalize_arxiv`` / ``normalize_title``
* 身份键提取：``identity_keys`` —— 一篇文献的一组“硬身份”键
* 精确去重：``dedupe_items`` —— union-find 合并同键条目；**同一身份
  scheme（如 doi）出现不一致的精确值时拒绝合并**，避免把两篇不同文献
  误判为重复（保守策略：宁可漏合并，不可错合并）。
"""
from __future__ import annotations

import re
import unicodedata

# DOI 归一：剥前缀后再做小写/去空白；DOI 本身不区分大小写。
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)
# arXiv 归一：剥 "arXiv:" 前缀；版本号 "v2" 可选剥离。
_ARXIV_PREFIX_RE = re.compile(r"^arxiv:", re.IGNORECASE)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)
# 标题归一：去掉所有标点与空白后比较。
_TITLE_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]", re.UNICODE)


def normalize_doi(raw: str | None) -> str:
    """归一化 DOI：去空白、小写、剥离 ``doi:`` / ``https://doi.org/`` 前缀。"""
    if not raw:
        return ""
    s = str(raw).strip()
    s = _DOI_PREFIX_RE.sub("", s).strip()
    return s.casefold()


def normalize_arxiv(raw: str | None, *, strip_version: bool = True) -> str:
    """归一化 arXiv id：小写、剥 ``arxiv:`` 前缀、可选剥版本号（``v2``）。"""
    if not raw:
        return ""
    s = str(raw).strip()
    s = _ARXIV_PREFIX_RE.sub("", s).strip()
    if strip_version:
        s = _ARXIV_VERSION_RE.sub("", s)
    return s.casefold()


def normalize_title(raw: str | None) -> str:
    """归一化标题：NFKC、小写、去除全部标点与空白。"""
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).casefold()
    return _TITLE_PUNCT_RE.sub("", s)


def _first_author(authors) -> str:
    """取第一作者姓氏（归一化）；缺失返回空串。"""
    if not authors:
        return ""
    if isinstance(authors, str):
        first = authors.split(";")[0].split(",")[0]
    else:
        first = str(authors[0]) if authors else ""
    return re.sub(r"\s+", "", first).casefold()


def identity_keys(item: dict) -> set[str]:
    """提取一篇文献的身份键集合。

    ``item`` 可含：``doi`` / ``arxiv`` / ``title`` / ``year`` / ``authors``。
    返回形如 ``{"doi:10.1/x", "arxiv:2101.0001"}`` 的键集，外加复合键
    ``tiyr:{归一标题}|{年}|{第一作者}``——**标题、年份、第一作者三者缺一
    即不建复合键**，防止模糊合并。
    """
    keys: set[str] = set()
    doi = normalize_doi(item.get("doi"))
    if doi:
        keys.add(f"doi:{doi}")
    arxiv = normalize_arxiv(item.get("arxiv"))
    if arxiv:
        keys.add(f"arxiv:{arxiv}")

    title = normalize_title(item.get("title"))
    year = str(item.get("year") or "").strip()
    author = _first_author(item.get("authors"))
    if title and year and author:
        keys.add(f"tiyr:{title}|{year}|{author}")
    return keys


_SCHEMES = ("doi:", "arxiv:", "tiyr:")


def _scheme_values(keys: set[str], scheme: str) -> set[str]:
    return {k for k in keys if k.startswith(scheme)}


def _conflict(ka: set[str], kb: set[str]) -> bool:
    """同一 scheme 下两边都有精确键但无交集 → 身份冲突，拒绝合并。"""
    for scheme in _SCHEMES:
        va, vb = _scheme_values(ka, scheme), _scheme_values(kb, scheme)
        if va and vb and not (va & vb):
            return True
    return False


def dedupe_items(items: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """精确去重：union-find 合并共享身份键的条目。

    合并规则：共享任一身份键即合并候选，但若两条目在同一 scheme 上有
    **不一致的精确值**（如 doi 不同）则拒绝合并。组内保留首条（原顺序），
    返回 ``(去重后列表, 被合并条目id → 保留条目id 映射)``。条目 id 取
    ``id`` / ``identifier`` 字段，缺失时用下标。
    """
    n = len(items)
    if n == 0:
        return [], {}

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    def uid(i: int) -> str:
        it = items[i] or {}
        return str(it.get("id") or it.get("identifier") or i)

    keys = [identity_keys(it or {}) for it in items]
    key_index: dict[str, list[int]] = {}
    for i, ks in enumerate(keys):
        for k in ks:
            key_index.setdefault(k, []).append(i)

    for idxs in key_index.values():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                a, b = idxs[x], idxs[y]
                if not _conflict(keys[a], keys[b]):
                    union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out: list[dict] = []
    merged_map: dict[str, str] = {}
    for idxs in sorted(groups.values(), key=lambda g: g[0]):
        kept = idxs[0]
        out.append(items[kept])
        kept_id = uid(kept)
        for i in idxs[1:]:
            merged_map[uid(i)] = kept_id
    return out, merged_map
