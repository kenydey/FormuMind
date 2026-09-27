"""Task 2.3: CitationAnchor contract — structured citation anchor dataclass/model.

Downstream RAG answers need to bind [^n] references to specific chunk offsets.
CitationAnchor provides a standardised representation and conversion helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Number of leading characters used as a chunk text summary.
TEXT_PREVIEW_LEN = 200


@dataclass
class CitationLocator:
    """W4-6 · P0-15: 可选引用定位器 —— 页码 / 图号 / 表号。

    Schema（供 W4-2 ``evidence_json`` 直接复用）::

        {"page": 3, "figure": "2", "table": null}

    全部可选；全空视为无定位器（``from_dict`` 返回 None）。
    """

    page: int | None = None
    figure: str | None = None  # 如 "3" / "3a"
    table: str | None = None  # 如 "2"

    def to_dict(self) -> dict[str, Any]:
        """紧凑 dict：只保留非 None 字段。"""
        return {
            k: v
            for k, v in (
                ("page", self.page),
                ("figure", self.figure),
                ("table", self.table),
            )
            if v is not None
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CitationLocator | None:
        """从 manifest item / evidence_json 的 dict 恢复；无效/全空 → None。"""
        if not isinstance(data, dict):
            return None
        page = data.get("page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        figure = data.get("figure")
        table = data.get("table")
        loc = cls(
            page=page,
            figure=str(figure).strip() if figure else None,
            table=str(table).strip() if table else None,
        )
        if loc.page is None and not loc.figure and not loc.table:
            return None
        return loc


@dataclass
class CitationAnchor:
    """A structured citation anchor binding a footnote reference to a chunk.

    Created from a document chunk (ORM row or Pydantic response) and renders
    into markdown reference text plus a human-readable citation line.
    """

    chunk_id: str
    source_id: str
    text: str
    page: int | None = None
    paragraph: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    # W4-6 · P0-15: 可选精确定位器（图/表/页）；None = 未指定。
    locator: CitationLocator | None = None

    @classmethod
    def from_chunk_row(cls, row: Any) -> CitationAnchor:
        """Construct a CitationAnchor from an ORM ``DocumentChunk`` row or
        a Pydantic ``DocumentChunkResponse``.

        Handles the field-name differences between the two types:

        * ORM: ``page_no`` / ``paragraph_idx``
        * Pydantic: ``page`` / ``paragraph``
        """
        # Resolve page — try Pydantic layout first, then ORM.
        page: int | None = None
        for attr in ("page", "page_no"):
            val = getattr(row, attr, None)
            if val is not None:
                page = int(val)
                break

        # Resolve paragraph index.
        paragraph: int | None = None
        for attr in ("paragraph", "paragraph_idx"):
            val = getattr(row, attr, None)
            if val is not None:
                paragraph = int(val)
                break

        text = (row.text or "")[:TEXT_PREVIEW_LEN]

        return cls(
            chunk_id=row.id,
            source_id=row.source_id,
            text=text,
            page=page,
            paragraph=paragraph,
            offset_start=(
                int(row.offset_start) if getattr(row, "offset_start", None) is not None else None
            ),
            offset_end=(
                int(row.offset_end) if getattr(row, "offset_end", None) is not None else None
            ),
        )

    def to_reference(self, n: int) -> str:
        """Render a Markdown footnote reference, e.g. ``[^1]``."""
        return f"[^{n}]"

    def to_citation_text(self) -> str:
        """Render a human-readable citation line.

        Examples::

            Source: doc.pdf, pp. 3, ¶2
            Source: doc.pdf
        """
        parts: list[str] = [f"Source: {self.source_id}"]
        extras: list[str] = []
        # W4-6 · P0-15: locator.page 作为 page 的回退；图/表号追加展示。
        page = self.page
        if page is None and self.locator is not None:
            page = self.locator.page
        if page is not None:
            extras.append(f"pp. {page}")
        if self.paragraph is not None:
            extras.append(f"¶{self.paragraph}")
        if self.locator is not None:
            if self.locator.figure:
                extras.append(f"Fig. {self.locator.figure}")
            if self.locator.table:
                extras.append(f"Table {self.locator.table}")
        if extras:
            parts.append(", ".join(extras))
        return ", ".join(parts)
