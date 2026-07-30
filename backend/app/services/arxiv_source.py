"""arXiv LaTeX source acquisition — ``/e-print/`` instead of ``/pdf/``.

arXiv serves every submission's original source at
``https://arxiv.org/e-print/{id}``, and for a RAG corpus that is strictly
better than the rendered PDF. Measured on a 100-page paper:

| path         | download        | parse     | total    |
|--------------|-----------------|-----------|----------|
| PDF          | 1.17 s / 5.2 MB | **51.7 s**| ~53 s    |
| LaTeX source | 0.94 s / 3.9 MB | ~0.3 s    | ~1.2 s   |

The parse figure is the whole story: most of those 51.7 s were RapidOCR firing
on seven figure-heavy pages, because a page that is mostly graphics looks like
a scan to the triage heuristic. Source text needs no OCR at all.

Fidelity improves too, in the two places that matter for retrieval:

* **Maths** survives as LaTeX — ``$L(C) = aC^b + c$`` rather than the PDF
  parser's ``_L_ ( _C_ ) = _aC_<sup>_b_</sup> + _c,_``.
* **Structure** is explicit: ``\\section{}`` becomes a Markdown heading, so
  ``chunk_markdown`` puts the heading path into chunk titles exactly as it does
  for every other ingest route. Tables become pipe tables and display maths
  becomes ``$$`` blocks *because that is what the chunker keeps atomic* — a
  space-aligned table (what ``latex2text`` emits by default) would be split
  mid-row.

Not every submission has usable source: PDF-only submissions exist, and
``pylatexenc`` — otherwise a clean zero-dependency choice — crashes on real
papers (measured: 1 of 4, an ``IndexError`` on a single-argument ``\\href``).
Both cases degrade rather than fail: conversion runs **per section**, so a
crash costs one section instead of the document, and the caller falls back to
the PDF path when nothing usable comes out.
"""
from __future__ import annotations

import gzip
import io
import logging
import re
import tarfile

import httpx

from .errors import degrade_return, log_handled_exception

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "FormuMind/1.0 (research platform; arXiv source fetcher)"}

# Archive limits. The bytes come off the network, so an unbounded extract is a
# memory-exhaustion vector on a 2.2 GB host regardless of intent.
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_TEX_BYTES = 8 * 1024 * 1024
_MAX_MEMBERS = 2000
_MAX_INPUT_DEPTH = 8

# Files that are markup machinery, not prose. Concatenating a .sty into the
# body is how a "full text" ends up full of macro definitions.
_SKIP_SUFFIXES = (".sty", ".cls", ".bst", ".bbl", ".clo", ".def", ".cfg")

_SECTION_RE = re.compile(r"\\(section|subsection|subsubsection|paragraph)\*?\s*\{")
_HEADING_LEVEL = {"section": "##", "subsection": "###", "subsubsection": "####", "paragraph": "#####"}

# Display-math environments worth preserving verbatim; the chunker treats a
# block opening with `$$` as atomic.
_DISPLAY_MATH_ENVS = ("equation", "equation*", "align", "align*", "gather", "gather*", "multline", "eqnarray")


def eprint_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/e-print/{arxiv_id.strip()}"


def fetch_eprint(arxiv_id: str, timeout: float = 20.0) -> bytes | None:
    """Download the raw source archive, or None on any failure."""
    url = eprint_url(arxiv_id)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HEADERS) as client:
            r = client.get(url)
        if int(getattr(r, "status_code", 0)) != 200:
            return None
        content = r.content
        if not content or len(content) > _MAX_ARCHIVE_BYTES:
            logger.info("arxiv e-print skipped (%d bytes): %s", len(content or b""), arxiv_id)
            return None
        return content
    except Exception as exc:
        return degrade_return(logger, exc, f"arxiv e-print fetch failed: {url}", None)


# ── archive → .tex files ─────────────────────────────────────────────────────


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_tex_files(blob: bytes) -> dict[str, str]:
    """Source archive → ``{member name: text}`` for the ``.tex`` members only.

    Handles both shapes arXiv serves: a gzipped tar (the common case) and a
    single gzipped ``.tex``. Members are read into memory and never written to
    disk, so archive path traversal has nothing to act on; sizes are still
    capped because the archive is untrusted input.
    """
    files: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            total = 0
            for i, member in enumerate(tar):
                if i >= _MAX_MEMBERS or total >= _MAX_TEX_BYTES:
                    logger.info("arxiv archive truncated at member %d", i)
                    break
                name = member.name
                if not member.isfile() or not name.lower().endswith(".tex"):
                    continue
                if name.lower().endswith(_SKIP_SUFFIXES):
                    continue
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                raw = fh.read(_MAX_TEX_BYTES - total)
                total += len(raw)
                files[name] = _decode(raw)
        return files
    except tarfile.TarError as exc:
        log_handled_exception(logger, exc, "not a tar archive — trying bare gzip")

    # Single gzipped .tex: arXiv does this for one-file submissions.
    try:
        raw = gzip.decompress(blob)
    except Exception as exc:
        return degrade_return(logger, exc, "arxiv archive is neither tar nor gzip", {})
    if len(raw) > _MAX_TEX_BYTES:
        raw = raw[:_MAX_TEX_BYTES]
    text = _decode(raw)
    return {"main.tex": text} if "\\" in text else {}


def _pick_main(files: dict[str, str]) -> str | None:
    """The file that is the document root.

    ``\\documentclass`` is the reliable marker. Falling back to a name guess
    matters because concatenating in arbitrary order scrambles section order —
    the whole point of using the source is that the structure is right.
    """
    roots = [n for n, t in files.items() if "\\documentclass" in t]
    if roots:
        # Shallowest path wins; arXiv puts the root at the top level.
        return min(roots, key=lambda n: (n.count("/"), len(n)))
    for guess in ("main.tex", "ms.tex", "paper.tex", "article.tex"):
        for name in files:
            if name.rsplit("/", 1)[-1].lower() == guess:
                return name
    return next(iter(files), None)


def _resolve_include(target: str, files: dict[str, str]) -> str | None:
    """Match an ``\\input{}`` target against archive member names.

    The directive usually omits the ``.tex`` suffix and may be written relative
    to the root, so compare on both the full path and the bare basename.
    """
    cand = target.strip().strip('"')
    if not cand.lower().endswith(".tex"):
        cand += ".tex"
    for name in (cand, cand.lstrip("./")):
        if name in files:
            return name
    base = cand.rsplit("/", 1)[-1].lower()
    for name in files:
        if name.rsplit("/", 1)[-1].lower() == base:
            return name
    return None


_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]*)\}")


def assemble_source(files: dict[str, str]) -> str:
    """Expand ``\\input`` / ``\\include`` from the root file, in document order."""
    main = _pick_main(files)
    if not main:
        return ""

    def expand(name: str, depth: int, seen: frozenset[str]) -> str:
        if depth > _MAX_INPUT_DEPTH or name in seen:
            return ""  # cyclic or pathological nesting
        text = files.get(name, "")

        def sub(m: re.Match[str]) -> str:
            target = _resolve_include(m.group(1), files)
            if not target:
                return ""
            return "\n" + expand(target, depth + 1, seen | {name}) + "\n"

        return _INPUT_RE.sub(sub, text)

    return expand(main, 0, frozenset())


# ── LaTeX → Markdown ─────────────────────────────────────────────────────────


_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)

# Purely typographic macros. `latex2text` does not know them, so it keeps their
# arguments as text and `\rule{0pt}{2.0ex}Self-Attention` arrives as
# `0pt2.0exSelf-Attention` — glued onto a term someone would search for.
_LAYOUT_MACRO_RE = re.compile(
    r"\\(?:rule|vspace|hspace|vskip|hskip|phantom|hphantom|vphantom|"
    r"resizebox|scalebox|adjustbox|cline|setlength|addtolength|arraystretch|"
    r"centering|footnotesize|scriptsize|tiny|small|normalsize|large|Large|LARGE|huge|Huge)"
    r"\*?\s*(?:\{[^{}]*\}|\[[^\]]*\]|<[^>]*>)*"
)

# `\multicolumn{2}{c}{Total solids}` and `\multirow{2}{*}{Binder}` are span
# directives whose LAST argument is the cell text — stripping all of their
# arguments would delete table headers, so they keep the tail.
_SPAN_MACRO_RE = re.compile(r"\\(?:multicolumn|multirow)\*?\s*\{[^{}]*\}\s*(?:\{[^{}]*\}|\[[^\]]*\])\s*(?=\{)")


def strip_layout_macros(tex: str) -> str:
    """Remove typographic macros whose arguments are sizes, not prose."""
    tex = _SPAN_MACRO_RE.sub(" ", tex)
    return _LAYOUT_MACRO_RE.sub(" ", tex)


def strip_comments(tex: str) -> str:
    """Drop ``%`` comments, keeping escaped ``\\%`` (a literal percent sign).

    Percent signs are load-bearing in this corpus — "30 to 40 percent PVC" is
    routinely written ``30\\%``, and eating it changes the number's meaning.
    """
    return _COMMENT_RE.sub("", tex)


def _brace_group(s: str, i: int) -> tuple[str, int]:
    """Contents of the brace group starting at *i*, and the index after it."""
    depth, start = 1, i
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i], i + 1
        i += 1
    return s[start:], i


def _env_body(tex: str, env: str) -> list[tuple[int, int, str]]:
    """Spans and bodies of every ``\\begin{env}…\\end{env}`` occurrence."""
    out: list[tuple[int, int, str]] = []
    open_re = re.compile(r"\\begin\s*\{" + re.escape(env) + r"\}")
    close = "\\end{" + env + "}"
    for m in open_re.finditer(tex):
        end = tex.find(close, m.end())
        if end == -1:
            continue
        out.append((m.start(), end + len(close), tex[m.end() : end]))
    return out


# Placeholder for a pre-converted block. It must survive `latex2text`
# untouched, so it is plain alphanumerics: the converter rewrites LaTeX
# ligatures, and `---` in a Markdown separator row comes out as an em dash —
# which silently turns a table back into ordinary prose.
_BLOCK_TOKEN = "ZZFMBLOCK{}ZZ"
_BLOCK_TOKEN_RE = re.compile(r"ZZFMBLOCK(\d+)ZZ")


def _table_markdown(body: str, cell_to_text) -> str:
    """One ``tabular`` body → Markdown pipe table rows."""
    # Drop the column spec that follows \begin{tabular}
    body = re.sub(r"^\s*(\{[^{}]*\}|\[[^\]]*\])+", "", body, count=1)
    for rule in (r"\toprule", r"\midrule", r"\bottomrule", r"\hline", r"\endhead", r"\\\\"):
        body = body.replace(rule, "\n" if rule == r"\\\\" else "")
    rows = [r.strip() for r in re.split(r"\\\\|\n", body) if r.strip()]
    cells = [[cell_to_text(c).strip() for c in re.split(r"(?<!\\)&", r)] for r in rows]
    cells = [r for r in cells if any(x for x in r)]
    if not cells:
        return ""
    width = max(len(r) for r in cells)
    lines = ["| " + " | ".join((r + [""] * width)[:width]) + " |" for r in cells]
    # A header separator is what makes it a table rather than lines of pipes.
    lines.insert(1, "|" + "---|" * width)
    return "\n".join(lines)


def extract_blocks(tex: str, cell_to_text) -> tuple[str, list[str]]:
    """Pull tables and display maths out, leaving numbered placeholders.

    They are converted here rather than after ``latex2text`` because that pass
    rewrites the very characters the Markdown forms depend on. Cell contents
    still go through *cell_to_text*, so ``\\textbf{}`` inside a header is
    handled.
    """
    blocks: list[str] = []

    def replace(env_names: tuple[str, ...], render) -> None:
        nonlocal tex
        for env in env_names:
            for start, end, body in reversed(_env_body(tex, env)):
                rendered = render(body)
                if not rendered:
                    tex = tex[:start] + "\n\n" + tex[end:]
                    continue
                blocks.append(rendered)
                token = _BLOCK_TOKEN.format(len(blocks) - 1)
                tex = tex[:start] + f"\n\n{token}\n\n" + tex[end:]

    replace(("tabular", "tabularx", "longtable"), lambda b: _table_markdown(b, cell_to_text))

    def math(body: str) -> str:
        body = re.sub(r"\\label\s*\{[^}]*\}", "", body).strip()
        return f"$$\n{body}\n$$" if body else ""

    replace(_DISPLAY_MATH_ENVS, math)
    return tex, blocks


def restore_blocks(text: str, blocks: list[str]) -> str:
    """Put the pre-converted tables and maths back where their tokens are."""

    def sub(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return f"\n\n{blocks[idx]}\n\n" if 0 <= idx < len(blocks) else ""

    return _BLOCK_TOKEN_RE.sub(sub, text)


# Environments that carry no prose worth indexing, or whose content is better
# dropped than rendered (float bodies keep their captions via \caption).
_DROP_ENVS = ("figure", "figure*", "thebibliography", "tikzpicture", "lstlisting", "verbatim")


def _drop_envs(tex: str) -> str:
    for env in _DROP_ENVS:
        for start, end, body in reversed(_env_body(tex, env)):
            caps = re.findall(r"\\caption\s*\{", body)
            keep = ""
            if caps:
                # Keep captions: for a figure they are often the only text that
                # says what the figure shows.
                for m in re.finditer(r"\\caption\s*\{", body):
                    text, _ = _brace_group(body, m.end())
                    keep += text.strip() + "\n\n"
            tex = tex[:start] + ("\n\n" + keep if keep else "\n\n") + tex[end:]
    return tex


_FALLBACK_STRIP_RE = re.compile(r"\\(?:[a-zA-Z]+\*?|.)\s*(?:\[[^\]]*\])?")


def _fallback_to_text(latex: str) -> str:
    """Dependency-free cleanup for when ``latex2text`` raises.

    Deliberately crude — it keeps brace contents and drops command names, which
    is wrong for a few macros but never loses a paragraph. The alternative on a
    crash is losing the section entirely.
    """
    text = re.sub(r"\\(?:cite|citep|citet|ref|eqref|label|autoref)\s*\{[^}]*\}", "", latex)
    text = re.sub(r"\\(?:textbf|textit|emph|texttt|textsc|mathrm|mbox)\s*\{([^{}]*)\}", r"\1", text)
    text = _FALLBACK_STRIP_RE.sub(" ", text)
    text = text.replace("{", " ").replace("}", " ").replace("~", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _latex_to_text(latex: str) -> str:
    """``latex2text`` when it works, the crude stripper when it does not."""
    try:
        from pylatexenc.latex2text import LatexNodes2Text
    except Exception as exc:  # pylatexenc is optional
        log_handled_exception(logger, exc, "pylatexenc unavailable — using fallback stripper")
        return _fallback_to_text(latex)
    try:
        # math_mode="verbatim" keeps $…$ intact, which is the point: an equation
        # is signal for retrieval, and latex2text's unicode rendering loses it.
        return LatexNodes2Text(math_mode="verbatim", keep_comments=False).latex_to_text(latex)
    except Exception as exc:
        log_handled_exception(logger, exc, "latex2text failed on a section — using fallback stripper")
        return _fallback_to_text(latex)


def _split_sections(tex: str) -> list[tuple[str, str, str]]:
    """``[(markdown heading prefix, title, body)]`` in document order.

    Splitting before conversion is what makes a ``latex2text`` crash survivable:
    it costs one section instead of the whole paper.
    """
    segs: list[list[str]] = []
    pos = 0
    preamble_end = 0
    for m in _SECTION_RE.finditer(tex):
        title, after = _brace_group(tex, m.end())
        if segs:
            segs[-1][2] = tex[pos:m.start()]
        else:
            preamble_end = m.start()
        segs.append([_HEADING_LEVEL[m.group(1)], title, ""])
        pos = after
    if segs:
        segs[-1][2] = tex[pos:]
        head = tex[:preamble_end]
    else:
        head = tex
    out: list[tuple[str, str, str]] = []
    # The abstract sits in the preamble region, before the first \section.
    abstracts = _env_body(head, "abstract")
    if abstracts:
        out.append(("##", "Abstract", abstracts[0][2]))
    out.extend((lvl, title, body) for lvl, title, body in segs)
    return out


def _cell_to_text(cell: str) -> str:
    """Inline conversion for a table cell — newlines would break the row."""
    return re.sub(r"\s+", " ", _latex_to_text(cell)).strip()


def _body_to_markdown(body: str) -> str:
    body = _drop_envs(body)
    body, blocks = extract_blocks(body, _cell_to_text)
    text = _latex_to_text(body)
    text = restore_blocks(text, blocks)
    # Collapse runs of spaces but never touch newlines: the pipe rows and $$
    # fences just restored depend on staying on their own lines.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def latex_to_markdown(tex: str) -> str:
    """Assembled LaTeX → Markdown for the standard chunking pipeline."""
    tex = strip_layout_macros(strip_comments(tex))
    parts: list[str] = []
    for prefix, raw_title, body in _split_sections(tex):
        title = _latex_to_text(raw_title).strip().replace("\n", " ")
        md_body = _body_to_markdown(body)
        if not md_body and not title:
            continue
        parts.append(f"{prefix} {title}".rstrip() if title else "")
        if md_body:
            parts.append(md_body)
    return "\n\n".join(p for p in parts if p).strip()


def fetch_arxiv_markdown(arxiv_id: str, timeout: float = 20.0) -> str | None:
    """arXiv id → Markdown full text from the LaTeX source, or None."""
    blob = fetch_eprint(arxiv_id, timeout)
    if not blob:
        return None
    files = extract_tex_files(blob)
    if not files:
        logger.info("arxiv %s: archive holds no .tex (PDF-only submission?)", arxiv_id)
        return None
    tex = assemble_source(files)
    if not tex.strip():
        return None
    md = latex_to_markdown(tex)
    return md or None
