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
* a table keeps its caption: the short ``表 N …`` / ``Table N …`` paragraph right above it is part of
  the table's chunk. A table is its own chunk, so the caption used to become a separate chunk of a few
  words (dropped outright when under the length floor) and the table — numbers with no subject — was left
  to be retrieved without the words that say what it is;
* ``<!-- page:N -->`` markers (inserted by the PDF parsers between pages) are
  consumed into ``Chunk.page_no`` provenance and stripped from chunk text;
* plain text without headings degrades to the legacy recursive splitter
  (``\\n\\n`` → ``\\n`` → sentence), so non-Markdown parsers keep behaving
  exactly as before (page-marked plain text is split page-wise first).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_OPEN_RE = re.compile(r"^(#{1,6})(\s+)")
_MAX_HEADING_PATH = 80


def _parse_heading(line: str) -> tuple[int, str] | None:
    """``## Title ##`` → ``(2, "Title")``; ``None`` when the line is not an ATX heading.

    The same answer as the regex ``^(#{1,6})\s+(.+?)\s*#*\s*$`` this replaces, in linear time: that pattern has two
    adjacent ``\s*`` around an optional ``#*`` and a lazy title in front, so a heading followed by a long run of
    spaces (padding from a layout-preserving PDF conversion) was retried from every position — cubic. 2000 trailing
    spaces took longer than 20 s; a differential test against the old pattern is in ``test_chunking_linear.py``.
    """
    opened = _HEADING_OPEN_RE.match(line)
    if not opened:
        return None
    level, gap = len(opened.group(1)), opened.group(2)
    rest = line[opened.end():]
    if not rest:
        # the old pattern's title needed a character, so it took the last whitespace character of the gap; one
        # whitespace character after the hashes therefore was not a heading, two or more were (with an empty title)
        return (level, "") if len(gap) >= 2 else None
    trimmed = rest.rstrip()
    title = trimmed.rstrip("#").rstrip()
    if title:
        return level, title
    if trimmed:  # nothing but closing hashes: the first one stays as the title
        return level, trimmed[0]
    return level, ""

PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*page:(\d+)\s*-->\s*$")
BLOCK_MARKER_RE = re.compile(
    r"^\s*<!--\s*block:(text|table|formula|figure|code|image)\s*-->\s*$"
)


def page_marker(page_no: int) -> str:
    """The canonical inter-page marker PDF parsers emit (chunker strips it)."""
    return f"<!-- page:{page_no} -->"


def block_marker(kind: str) -> str:
    """Block-kind marker layout-aware parsers emit (MinerU tier, Phase 1).

    Lets the chunker use the detected block type instead of the markdown
    heuristic. Stripped from chunk text like page markers.
    """
    return f"<!-- block:{kind} -->"


@dataclass
class Chunk:
    text: str
    heading_path: str = ""
    page_no: int | None = None
    paragraph_idx: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    # Layout provenance (Phase 0): normalized page-fraction bbox, filled by
    # layout-aware parsers (MinerU, Phase 1); None for the text chain.
    bbox: list | None = None
    # Block kind: text | table | formula | figure | code. Heuristic on
    # markdown for the text chain; layout parsers overwrite it.
    block_type: str = "text"


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
        heading = None if in_fence else _parse_heading(line)
        if heading:
            level, title = heading
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
        if BLOCK_MARKER_RE.match(line):
            # block-kind markers likewise become standalone blocks; the
            # chunker consumes them into Chunk.block_type and strips them.
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


def _classify_block_type(block: str) -> str:
    """Heuristic block kind for the text chain: table | formula | figure | code | text.

    Mirrors ``_is_atomic``'s classification; layout-aware parsers (MinerU,
    Phase 1) overwrite this with detected values. Kept deliberately coarse —
    the text chain has no real geometry, so this only separates "probably a
    table/formula" from running text for retrieval-time filtering.
    """
    first = block.lstrip()
    lower = first[:64].lower()
    if first.startswith("|") or lower.startswith("<table"):
        return "table"
    if (
        first.startswith("$$")
        or first.startswith("\\[")
        or first.startswith("\\begin{")
    ):
        return "formula"
    if first.startswith("```"):
        return "code"
    if first.startswith("!["):
        return "figure"
    return "text"


def _is_atomic(block: str) -> bool:
    """Blocks that must not be split on blank lines (tables / code / math).

    P1 #17: MinerU / HTML pipelines often emit ``<table>…</table>``; treating
    them as atomic prevents blank-line splits from shredding rows.
    """
    first = block.lstrip()
    lower = first[:64].lower()
    return (
        first.startswith("|")
        or first.startswith("```")
        or first.startswith("$$")
        or first.startswith("\\[")
        or first.startswith("\\begin{")
        or lower.startswith("<table")
        or "<table" in block[:200].lower()
    )


_CAPTION_START_RE = re.compile(r"^\s*(?:表|Table|TABLE|Tab\.)\s*[\dIVXivx]+", re.UNICODE)
_CAPTION_MAX_CHARS = 300


def _is_table_block(block: str) -> bool:
    first = block.lstrip()
    return first.startswith("|") or "<table" in first[:200].lower()


def _is_table_caption(paragraph: str) -> bool:
    """A short paragraph that *starts* with ``表 N`` / ``Table N`` — what a table's caption looks like.

    Anchored on purpose: "如表1所示，粘度符合要求" is a sentence about a table, not its caption.
    """
    text = paragraph.strip()
    if not text or len(text) > _CAPTION_MAX_CHARS or "\n\n" in text:
        return False
    text = re.sub(r"^#{1,6}\s+", "", text).replace("**", "").replace("__", "")
    return bool(_CAPTION_START_RE.match(text))


def _strip_block_markers(md: str) -> str:
    """Remove ``<!-- block:K -->`` lines (plain-text fallback paths)."""
    return "\n".join(
        line for line in md.split("\n") if not BLOCK_MARKER_RE.match(line)
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
            chunks: list[Chunk] = []
            char_pos = 0
            para_idx = 0
            for page_no, seg in pages:
                seg = _strip_block_markers(seg)
                for c in chunk_plain_text(seg, max_chars=max_chars, overlap=overlap):
                    chunks.append(Chunk(
                        c, "", page_no,
                        paragraph_idx=para_idx,
                        offset_start=char_pos,
                        offset_end=char_pos + len(c),
                        block_type=_classify_block_type(c),
                    ))
                    para_idx += 1
                    char_pos += len(c)
            return chunks
        chunks: list[Chunk] = []
        char_pos = 0
        md = _strip_block_markers(md)
        for i, c in enumerate(chunk_plain_text(md, max_chars=max_chars, overlap=overlap)):
            chunks.append(Chunk(
                c,
                paragraph_idx=i,
                offset_start=char_pos,
                offset_end=char_pos + len(c),
                block_type=_classify_block_type(c),
            ))
            char_pos += len(c)
        return chunks

    chunks: list[Chunk] = []
    page: int | None = None
    para_counter = 0
    char_pos = 0
    # Block kind announced by a layout-aware parser via <!-- block:K -->.
    # Overrides the markdown heuristic for the next emitted chunk only.
    pending_block: str | None = None
    for path, body in sections:
        current = ""
        current_page = page
        current_para = para_counter
        for block in _split_blocks(body):
            m = PAGE_MARKER_RE.match(block)
            if m:
                page = int(m.group(1))
                if not current.strip():
                    current_page = page
                continue
            bm = BLOCK_MARKER_RE.match(block)
            if bm:
                pending_block = bm.group(1)
                continue
            if _is_atomic(block):
                caption = ""
                if current.strip() and _is_table_block(block):
                    head, _, tail = current.rpartition("\n\n")
                    if _is_table_caption(tail):
                        caption, current = tail.strip(), head
                if current.strip():
                    clen = len(current.strip())
                    chunks.append(Chunk(
                        current.strip(), path, current_page,
                        paragraph_idx=current_para,
                        offset_start=char_pos,
                        offset_end=char_pos + clen,
                        block_type=pending_block or "text",
                    ))
                    char_pos += clen
                    current = ""
                    pending_block = None
                atom = f"{caption}\n\n{block}" if caption else block
                alen = len(atom)
                chunks.append(Chunk(
                    atom, path, page,
                    paragraph_idx=para_counter,
                    offset_start=char_pos,
                    offset_end=char_pos + alen,
                    block_type=pending_block or _classify_block_type(block),
                ))
                pending_block = None
                para_counter += 1
                char_pos += alen
                current_page = page
                continue
            if not current.strip():
                current_page = page
                current_para = para_counter
            if len(current) + len(block) + 2 <= max_chars:
                current = f"{current}\n\n{block}" if current else block
                para_counter += 1
                continue
            if current.strip():
                clen = len(current.strip())
                chunks.append(Chunk(
                    current.strip(), path, current_page,
                    paragraph_idx=current_para,
                    offset_start=char_pos,
                    offset_end=char_pos + clen,
                    block_type=pending_block or "text",
                ))
                char_pos += clen
                pending_block = None
            current_page = page
            current_para = para_counter
            if len(block) > max_chars:
                for c in chunk_plain_text(block, max_chars=max_chars, overlap=overlap):
                    chunks.append(Chunk(
                        c, path, page,
                        paragraph_idx=para_counter,
                        offset_start=char_pos,
                        offset_end=char_pos + len(c),
                        block_type=pending_block or _classify_block_type(c),
                    ))
                    char_pos += len(c)
                pending_block = None
                para_counter += 1
                current = ""
                continue
            else:
                current = block
            para_counter += 1
        if current.strip():
            clen = len(current.strip())
            chunks.append(Chunk(
                current.strip(), path, current_page,
                paragraph_idx=current_para,
                offset_start=char_pos,
                offset_end=char_pos + clen,
                block_type=pending_block or "text",
            ))
            char_pos += clen
            pending_block = None
    return chunks
