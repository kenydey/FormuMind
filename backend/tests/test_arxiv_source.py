"""arXiv LaTeX source ingest — archive handling, LaTeX → Markdown, degradation.

Fully offline: the network layer is monkeypatched and every archive is built
in-memory by the fixtures below.
"""
from __future__ import annotations

import gzip
import io
import tarfile

import pytest

from app.services import arxiv_source as a


def _targz(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


_MAIN = r"""
\documentclass{article}
\begin{document}
\begin{abstract}
A waterborne epoxy primer containing zinc phosphate for corrosion protection
of carbon steel in marine atmospheric service.
\end{abstract}
\section{Introduction}
Pigment volume concentration is held between 30 and 40\% to balance barrier
properties against film flexibility~\cite{smith2020}.
\input{methods}
\section{Conclusion}
The two-component system outperformed the solventborne control.
\end{document}
"""

_METHODS = r"""
\section{Methods}
\subsection{Formulation}
Curing used an amine adduct at $r = 1.05$ stoichiometry.
\begin{equation}
\label{eq:pvc}
\mathrm{PVC} = \frac{V_p}{V_p + V_b}
\end{equation}
\begin{table}[h]
\caption{Gloss versus PVC}
\begin{tabular}{lrr}
\toprule
Sample & PVC (\%) & Gloss \\
\midrule
A & 32 & 88 \\
B & 41 & 74 \\
\bottomrule
\end{tabular}
\end{table}
"""


# ── archive extraction ───────────────────────────────────────────────────────


def test_extract_tex_files_from_targz():
    blob = _targz({"main.tex": _MAIN, "methods.tex": _METHODS, "neurips.sty": "\\newcommand{\\x}{}"})
    files = a.extract_tex_files(blob)
    assert set(files) == {"main.tex", "methods.tex"}


def test_extract_tex_files_from_bare_gzip():
    """arXiv serves a single gzipped .tex for one-file submissions."""
    files = a.extract_tex_files(gzip.compress(_MAIN.encode()))
    assert list(files) == ["main.tex"]
    assert "zinc phosphate" in files["main.tex"]


def test_extract_tex_files_ignores_style_and_bibliography():
    """`.sty` and `.bbl` are markup machinery, not prose.

    Concatenating them is how a "full text" ends up full of macro definitions.
    """
    blob = _targz(
        {
            "main.tex": _MAIN,
            "macros.sty": "\\newcommand{\\eg}{e.g.}",
            "main.bbl": "\\bibitem{smith2020} Smith et al.",
            "fig.pdf": "%PDF-1.4 binary",
        }
    )
    assert set(a.extract_tex_files(blob)) == {"main.tex"}


def test_extract_tex_files_returns_empty_for_garbage():
    assert a.extract_tex_files(b"not an archive at all") == {}


def test_extract_tex_files_caps_member_size():
    """The archive is untrusted input; an unbounded extract is a memory bomb."""
    huge = "x" * (a._MAX_TEX_BYTES + 5000)
    files = a.extract_tex_files(_targz({"main.tex": huge}))
    assert sum(len(t) for t in files.values()) <= a._MAX_TEX_BYTES


# ── \input expansion and document order ──────────────────────────────────────


def test_assemble_source_expands_input_in_document_order():
    """Section order is the whole reason to use the source rather than the PDF.

    Concatenating the .tex files in archive order puts Methods after
    Conclusion, which then produces wrong heading paths for every chunk.
    """
    files = a.extract_tex_files(_targz({"main.tex": _MAIN, "methods.tex": _METHODS}))
    tex = a.assemble_source(files)
    assert tex.index("Introduction") < tex.index("Methods") < tex.index("Conclusion")


def test_assemble_source_picks_root_by_documentclass():
    """Root selection must not depend on member order or name sorting.

    `zz_root.tex` sorts last; picking the alphabetically-first file instead
    would start the document at Methods.
    """
    files = {"zz_root.tex": _MAIN, "methods.tex": _METHODS}
    tex = a.assemble_source(files)
    assert "\\documentclass" in tex
    assert tex.index("Introduction") < tex.index("Methods")


def test_assemble_source_survives_missing_include():
    tex = a.assemble_source({"main.tex": _MAIN})  # methods.tex absent
    assert "Introduction" in tex and "Conclusion" in tex


def test_assemble_source_survives_include_cycle():
    """A cycle must terminate rather than recurse until the stack dies."""
    files = {
        "main.tex": "\\documentclass{article}\\input{b}",
        "b.tex": "B body \\input{main}",
    }
    tex = a.assemble_source(files)
    assert "B body" in tex


def test_assemble_source_empty_input():
    assert a.assemble_source({}) == ""


# ── comments ─────────────────────────────────────────────────────────────────


def test_strip_comments_keeps_escaped_percent():
    """Percent signs are load-bearing: `30\\%` is a formulation number."""
    out = a.strip_comments(r"Solids at 45\% by weight  % TODO check this")
    assert r"45\%" in out
    assert "TODO" not in out


def test_strip_comments_removes_commented_out_markup():
    out = a.strip_comments("live text\n% \\begin{tabular}{ll}\nmore text")
    assert "tabular" not in out
    assert "live text" in out and "more text" in out


# ── LaTeX → Markdown ─────────────────────────────────────────────────────────


@pytest.fixture
def markdown() -> str:
    files = a.extract_tex_files(_targz({"main.tex": _MAIN, "methods.tex": _METHODS}))
    return a.latex_to_markdown(a.assemble_source(files))


def test_headings_become_markdown(markdown):
    assert "## Abstract" in markdown
    assert "## Introduction" in markdown
    assert "## Methods" in markdown
    assert "### Formulation" in markdown


def test_heading_order_matches_document(markdown):
    for a_, b_ in (("Abstract", "Introduction"), ("Introduction", "Methods"), ("Methods", "Conclusion")):
        assert markdown.index(f" {a_}") < markdown.index(f" {b_}")


def test_table_becomes_a_pipe_table(markdown):
    """`chunk_markdown` only keeps a block atomic when its lines start with `|`.

    latex2text's default space-aligned rendering is not recognised, so the
    table would be split mid-row — and half a table is useless.
    """
    assert "| Sample | PVC (%) | Gloss |" in markdown
    assert "|---|---|---|" in markdown
    assert "| A | 32 | 88 |" in markdown


def test_separator_row_survives_latex_conversion(markdown):
    """Regression: latex2text turns `---` into an em dash.

    That silently converted every table back into ordinary prose, so the
    conversion has to happen outside the latex2text pass.
    """
    assert "|—|" not in markdown
    assert "|---|" in markdown


def test_display_math_becomes_an_atomic_block(markdown):
    assert "$$" in markdown
    assert "V_p + V_b" in markdown
    assert "\\label" not in markdown


def test_inline_math_is_preserved(markdown):
    """Equations are retrieval signal; unicode-rendering them loses that."""
    assert "$r = 1.05$" in markdown


def test_citations_are_dropped(markdown):
    assert "smith2020" not in markdown
    assert "\\cite" not in markdown


def test_figure_captions_are_kept_but_float_markup_is_not(markdown):
    assert "Gloss versus PVC" in markdown  # \caption survived
    assert "\\begin{table}" not in markdown


def test_layout_macros_do_not_glue_onto_words():
    r"""`\rule{0pt}{2.0ex}Self-Attention` used to arrive as `0pt2.0exSelf-Attention`.

    latex2text does not know the macro, so it keeps the size arguments as text —
    corrupting a term someone would search for.
    """
    md = a.latex_to_markdown(r"\section{S}" + "\n" + r"\rule{0pt}{2.0ex}Self-Attention layer")
    assert "0pt2.0ex" not in md
    assert "Self-Attention layer" in md


def test_multicolumn_keeps_its_cell_text():
    r"""`\multicolumn{2}{c}{Total solids}` — the last argument is the header."""
    tex = (
        r"\section{S}\begin{tabular}{ll}"
        r"\multicolumn{2}{c}{Total solids} \\ A & 1 \\"
        r"\end{tabular}"
    )
    md = a.latex_to_markdown(tex)
    assert "Total solids" in md


# ── degradation ──────────────────────────────────────────────────────────────


def test_latex2text_crash_costs_one_section_not_the_document(monkeypatch):
    """pylatexenc crashes on real papers — measured 1 in 4 (IndexError on \\href).

    Conversion runs per section precisely so a crash degrades one section
    instead of the paper. The patch goes on pylatexenc itself, not on
    ``_latex_to_text`` — that function *is* the guard under test.

    The discriminator between the two converters is ``\\%``: latex2text renders
    it as a percent sign, the crude fallback drops it. So finding ``40%`` intact
    proves the Introduction still took the good path while Methods fell back.
    """
    from pylatexenc.latex2text import LatexNodes2Text

    real = LatexNodes2Text.latex_to_text

    def flaky(self, latex, *args, **kwargs):
        if "amine adduct" in latex:
            raise IndexError("list index out of range")
        return real(self, latex, *args, **kwargs)

    monkeypatch.setattr(LatexNodes2Text, "latex_to_text", flaky)
    files = a.extract_tex_files(_targz({"main.tex": _MAIN, "methods.tex": _METHODS}))
    md = a.latex_to_markdown(a.assemble_source(files))

    # The failing section still yields prose, via the fallback stripper.
    assert "amine adduct" in md
    # Sections either side are untouched and still fully converted.
    assert "zinc phosphate" in md
    assert "Conclusion" in md
    assert "40%" in md, "an unrelated section was degraded to the fallback path"


def test_fallback_stripper_keeps_prose_without_pylatexenc():
    out = a._fallback_to_text(
        r"Curing used an \textbf{amine adduct}~\cite{x} at 30\% solids."
    )
    assert "amine adduct" in out
    assert "cite" not in out


def test_missing_pylatexenc_degrades_instead_of_raising(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pylatexenc(name, *args, **kwargs):
        if name.startswith("pylatexenc"):
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pylatexenc)
    out = a._latex_to_text(r"Zinc phosphate at 30\% by \textbf{weight}.")
    assert "Zinc phosphate" in out


# ── fetch_arxiv_markdown ─────────────────────────────────────────────────────


def test_fetch_arxiv_markdown_end_to_end(monkeypatch):
    blob = _targz({"main.tex": _MAIN, "methods.tex": _METHODS})
    monkeypatch.setattr(a, "fetch_eprint", lambda *args, **kw: blob)
    md = a.fetch_arxiv_markdown("2401.00001")
    assert md and "## Introduction" in md


def test_fetch_arxiv_markdown_none_when_no_tex(monkeypatch):
    """A PDF-only submission must degrade so the caller can try the PDF."""
    monkeypatch.setattr(a, "fetch_eprint", lambda *args, **kw: _targz({"paper.pdf": "%PDF"}))
    assert a.fetch_arxiv_markdown("2401.00001") is None


def test_fetch_arxiv_markdown_none_when_download_fails(monkeypatch):
    monkeypatch.setattr(a, "fetch_eprint", lambda *args, **kw: None)
    assert a.fetch_arxiv_markdown("2401.00001") is None


def test_eprint_url_shape():
    assert a.eprint_url(" 2401.00001 ") == "https://arxiv.org/e-print/2401.00001"


def test_fetch_eprint_rejects_oversized_archive(monkeypatch):
    class _Resp:
        status_code = 200
        content = b"x" * (a._MAX_ARCHIVE_BYTES + 1)

    class _Client:
        def __init__(self, *a_, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a_):
            return False

        def get(self, *a_, **kw):
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _Client)
    assert a.fetch_eprint("2401.00001") is None


def test_fetch_eprint_returns_none_on_non_200(monkeypatch):
    class _Resp:
        status_code = 404
        content = b""

    class _Client:
        def __init__(self, *a_, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a_):
            return False

        def get(self, *a_, **kw):
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _Client)
    assert a.fetch_eprint("2401.00001") is None
