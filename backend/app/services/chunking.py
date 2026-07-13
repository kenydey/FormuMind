"""Structure-aware chunking — Markdown headings and tables survive splitting.

``chunk_markdown`` is the shared chunker for every ingest path.  Behaviour:

* the document is first sectioned on ATX headings (``#`` … ``######``); each
  chunk remembers its heading path (``实施例 > 实施例 3``) so retrieval and
  citations can show *where* in the document a passage lives;
* Markdown tables (and fenced code blocks) are atomic — they are never split
  mid-row, even when that makes a chunk oversized, because a half table is
  worthless for formulation extraction;
* display-math blocks (``$$…$$``, ``\\[…\\]``, ``\\begin{…}…\\end{…}``) are
  atomic too — chemistry PDFs parsed by Docling/MinerU carry reaction
  equations as LaTeX, and half an equation is as useless as half a table;
* ``<!-- page:N -->`` markers (inserted by the PDF parsers between pages) are
  consumed into ``Chunk.page_no`` provenance and stripped from chunk text;
* plain text without headings degrades to the legacy recursive splitter
  (``\\n\\n`` → ``\\n`` → sentence), so non-Markdown parsers keep behaving
  exactly as before (page-marked plain text is split page-wise first).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MAX_HEADING_PATH = 80

PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*page:(\d+)\s*-->\s*$")


def page_marker(page_no: int) -> str:
    """The canonical inter-page marker PDF parsers emit (chunker strips it)."""
    return f"<!-- page:{page_no} -->"


@dataclass
class Chunk:
    text: str
    heading_path: str = ""
    page_no: int | None = None


# ── legacy plain-text splitter (moved verbatim from ingestion) ───────────────


def chunk_plain_text(
    text: str,
    *,
    max_chars: int = 1600,
    overlap: int = 200,
    max_depth: int = 10,
    _depth: int = 0,
) -> list[str]:
    """Recursive split on \\n\\n > \\n > 句号，控制 chunk 大小。"""
    text = text.strip()
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    if _depth >= max_depth:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
        return chunks

    for sep in ("\n\n", "\n", "。", ". "):
        if sep not in text:
            continue
        parts = text.split(sep)
        chunks = []
        current = ""
        for i, part in enumerate(parts):
            piece = part if i == len(parts) - 1 else part + sep
            if len(current) + len(piece) <= max_chars:
                current += piece
            else:
                if current.strip():
                    chunks.append(current.strip())
                if len(piece) > max_chars:
                    chunks.extend(
                        chunk_plain_text(
                            piece,
                            max_chars=max_chars,
                            overlap=overlap,
                            max_depth=max_depth,
                            _depth=_depth + 1,
                        )
                    )
                    current = ""
                else:
                    current = piece
        if current.strip():
            chunks.append(current.strip())
        if chunks:
            return chunks

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# ── markdown structure parsing ───────────────────────────────────────────────


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Split on ATX headings → [(heading_path, body)]. Empty path = preamble."""
    lines = md.split("\n")
    sections: list[tuple[str, list[str]]] = [("", [])]
    stack: list[tuple[int, str]] = []  # (level, title)
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            path = " > ".join(t for _, t in stack)[:_MAX_HEADING_PATH]
            sections.append((path, []))
        else:
            sections[-1][1].append(line)
    return [(path, "\n".join(body).strip()) for path, body in sections if "\n".join(body).strip()]


def _math_open(stripped: str) -> str | None:
    """If *stripped* opens a display-math block, return the closer token."""
    if stripped.startswith("$$"):
        # single-line $$…$$ is already closed
        return None if stripped.count("$$") >= 2 and len(stripped) > 2 else "$$"
    if stripped.startswith("\\["):
        return None if stripped.endswith("\\]") and len(stripped) > 3 else "\\]"
    if stripped.startswith("\\begin{"):
        return "\\end{"
    return None


def _math_selfclosed(stripped: str) -> bool:
    return (
        (stripped.startswith("$$") and stripped.count("$$") >= 2 and len(stripped) > 2)
        or (stripped.startswith("\\[") and stripped.endswith("\\]") and len(stripped) > 3)
    )


def _split_blocks(body: str) -> list[str]:
    """Split a section body into blocks; tables, fences and math stay atomic."""
    lines = body.split("\n")
    blocks: list[str] = []
    current: list[str] = []
    mode = "text"  # text | table | fence | math
    math_closer = ""

    def flush() -> None:
        block = "\n".join(current).strip()
        if block:
            blocks.append(block)
        current.clear()

    for line in lines:
        stripped = line.strip()
        if mode == "fence":
            current.append(line)
            if stripped.startswith("```"):
                flush()
                mode = "text"
            continue
        if mode == "math":
            current.append(line)
            if math_closer in stripped:
                flush()
                mode = "text"
            continue
        if stripped.startswith("```"):
            flush()
            mode = "fence"
            current.append(line)
            continue
        if PAGE_MARKER_RE.match(line):
            # page markers become standalone blocks (consumed by chunk_markdown)
            flush()
            blocks.append(stripped)
            continue
        if _math_selfclosed(stripped):
            flush()
            blocks.append(stripped)
            continue
        closer = _math_open(stripped)
        if closer:
            flush()
            mode = "math"
            math_closer = closer
            current.append(line)
            continue
        is_table_row = stripped.startswith("|") and stripped.count("|") >= 2
        if mode == "table":
            if is_table_row:
                current.append(line)
                continue
            flush()
            mode = "text"
        if is_table_row:
            flush()
            mode = "table"
            current.append(line)
            continue
        if not stripped and current:
            flush()
            continue
        if stripped or current:
            current.append(line)
    flush()
    return blocks


def _is_atomic(block: str) -> bool:
    first = block.lstrip()
    return (
        first.startswith("|")
        or first.startswith("```")
        or first.startswith("$$")
        or first.startswith("\\[")
        or first.startswith("\\begin{")
    )


def _split_pages(md: str) -> list[tuple[int | None, str]]:
    """Split page-marked text into [(page_no, segment)]; markers removed."""
    segments: list[tuple[int | None, list[str]]] = [(None, [])]
    for line in md.split("\n"):
        m = PAGE_MARKER_RE.match(line)
        if m:
            segments.append((int(m.group(1)), []))
        else:
            segments[-1][1].append(line)
    return [(p, "\n".join(body).strip()) for p, body in segments if "\n".join(body).strip()]


def chunk_markdown(
    md: str, *, max_chars: int = 1600, overlap: int = 200
) -> list[Chunk]:
    """Structure-aware chunking; degrades to the plain splitter for non-Markdown."""
    md = (md or "").strip()
    if not md:
        return []

    sections = _split_sections(md)
    has_structure = (
        any(path for path, _ in sections) or "|" in md or "```" in md or "$$" in md
    )
    if not has_structure:
        pages = _split_pages(md)
        if len(pages) > 1 or (pages and pages[0][0] is not None):
            # Page-marked plain text (pypdf): split page-wise so provenance
            # survives even without headings.
            return [
                Chunk(c, "", page_no)
                for page_no, seg in pages
                for c in chunk_plain_text(seg, max_chars=max_chars, overlap=overlap)
            ]
        return [Chunk(c) for c in chunk_plain_text(md, max_chars=max_chars, overlap=overlap)]

    chunks: list[Chunk] = []
    page: int | None = None
    for path, body in sections:
        current = ""
        current_page = page
        for block in _split_blocks(body):
            m = PAGE_MARKER_RE.match(block)
            if m:
                page = int(m.group(1))
                if not current.strip():
                    current_page = page
                continue
            if _is_atomic(block):
                if current.strip():
                    chunks.append(Chunk(current.strip(), path, current_page))
                    current = ""
                # Tables/fences/math are never split — oversized ones ship whole.
                chunks.append(Chunk(block, path, page))
                current_page = page
                continue
            if not current.strip():
                current_page = page
            if len(current) + len(block) + 2 <= max_chars:
                current = f"{current}\n\n{block}" if current else block
                continue
            if current.strip():
                chunks.append(Chunk(current.strip(), path, current_page))
            current_page = page
            if len(block) > max_chars:
                chunks.extend(
                    Chunk(c, path, page)
                    for c in chunk_plain_text(block, max_chars=max_chars, overlap=overlap)
                )
                current = ""
            else:
                current = block
        if current.strip():
            chunks.append(Chunk(current.strip(), path, current_page))
    return chunks
