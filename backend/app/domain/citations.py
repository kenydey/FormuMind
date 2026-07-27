"""Task 2.3: CitationAnchor contract — structured citation anchor dataclass/model.

Downstream RAG answers need to bind [^n] references to specific chunk offsets.
CitationAnchor provides a standardised representation and conversion helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Number of leading characters used as a chunk text summary.
TEXT_PREVIEW_LEN = 200


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

    @classmethod
    def from_chunk_row(cls, row) -> CitationAnchor:
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
        if self.page is not None:
            extras.append(f"pp. {self.page}")
        if self.paragraph is not None:
            extras.append(f"¶{self.paragraph}")
        if extras:
            parts.append(", ".join(extras))
        return ", ".join(parts)
