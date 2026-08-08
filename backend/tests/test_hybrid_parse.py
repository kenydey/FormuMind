"""Triage and fusion: what gets escalated, what gets looked at, what survives.

Three claims carry this design, and each is asserted by counting calls rather
than by inspecting output — a routing decision is only observable as a call
that did or did not happen:

* pages the local parser handled well are **never** sent to a metered API;
* `equation` and `table` blocks are **never** sent to a vision model, because
  they already arrive as LaTeX and HTML and a guess would be a downgrade;
* no block is ever dropped, however badly its processing went.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import hybrid_parse, mineru_cloud, pdf_local


def _page(
    page_no: int,
    *,
    markdown: str = "body text",
    tables: int = 0,
    md_table: bool = False,
    image_ratio: float = 0.0,
    chars: int = 500,
) -> pdf_local.LocalPage:
    return pdf_local.LocalPage(
        page_no=page_no,
        markdown=markdown,
        char_count=chars,
        n_tables=tables,
        n_images=1 if image_ratio else 0,
        image_area_ratio=image_ratio,
        has_markdown_table=md_table,
    )


def _block(kind: str, **kw) -> mineru_cloud.MinerUBlock:
    return mineru_cloud.MinerUBlock(type=kind, **kw)


@pytest.fixture()
def cloud_on(monkeypatch: pytest.MonkeyPatch):
    """MinerU reachable, and every escalation recorded."""
    sent: list[int] = []

    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(
        pdf_local, "page_as_pdf",
        lambda content, page_no: sent.append(page_no) or b"%PDF one page",
    )
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes",
        lambda content, **kw: mineru_cloud.MinerUDocument(
            blocks=[_block("text", text="CLOUD TEXT")], markdown="cloud"
        ),
    )
    return sent


@pytest.fixture()
def no_vision(monkeypatch: pytest.MonkeyPatch):
    from app.services import vision_extract

    monkeypatch.setattr(vision_extract, "vision_available", lambda: (False, "未配置"))


# ── triage: what is worth paying for ─────────────────────────────────────────


def test_a_plain_text_page_is_not_escalated() -> None:
    assert hybrid_parse._needs_escalation(_page(1)) is False


def test_a_table_the_local_parser_rendered_is_not_escalated() -> None:
    """The check that keeps the bill proportional to the hard pages. A ruled
    table comes back as a perfectly good pipe table locally; paying a cloud
    parser to redo it is pure waste."""
    assert hybrid_parse._needs_escalation(
        _page(1, tables=1, md_table=True)
    ) is False


def test_a_table_the_local_parser_missed_is_escalated() -> None:
    assert hybrid_parse._needs_escalation(
        _page(1, tables=1, md_table=False)
    ) is True


def test_a_page_with_a_large_figure_is_escalated() -> None:
    """Chemical structures and reaction schemes are figures, not tables —
    routing on tables alone would miss every one of them."""
    assert hybrid_parse._needs_escalation(_page(1, image_ratio=0.30)) is True


def test_a_tiny_logo_is_not_escalated() -> None:
    assert hybrid_parse._needs_escalation(_page(1, image_ratio=0.01)) is False


def test_only_the_qualifying_pages_reach_the_cloud(
    cloud_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The economic claim, measured: a report with one hard page costs one
    page of quota, not the whole document."""
    pages = [
        _page(1),
        _page(2, tables=1, md_table=True),      # locally fine
        _page(3, image_ratio=0.4),              # a figure
        _page(4),
    ]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)

    hybrid_parse.parse(b"%PDF")
    assert cloud_on == [3], "only the figure page should have been sent"


def test_nothing_is_sent_when_the_document_is_all_prose(
    cloud_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: [_page(1), _page(2)])
    hybrid_parse.parse(b"%PDF")
    assert cloud_on == []


def test_escalation_is_capped_per_document(
    cloud_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200-page scan must not be able to swallow the day's quota by
    accident; the pages beyond the cap keep their local text."""
    monkeypatch.setattr(get_settings(), "mineru_max_pages_per_doc", 2, raising=False)
    pages = [_page(n, image_ratio=0.5) for n in range(1, 11)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)

    output = hybrid_parse.parse(b"%PDF")
    assert len(cloud_on) == 2
    assert output.count("<!-- page:") == 10        # every page still present
    assert "body text" in output                   # the uncapped ones kept theirs


# ── degradation: the upload must never depend on the cloud ───────────────────


def test_cloud_unavailable_gives_exactly_the_local_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [_page(1, image_ratio=0.5), _page(2)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (False, "未启用"))

    assert hybrid_parse.parse(b"%PDF") == pdf_local.assemble(
        [(p.page_no, p.markdown) for p in pages]
    )


def test_a_failed_escalation_keeps_the_local_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: [_page(1, markdown="LOCAL", image_ratio=0.5)])
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(pdf_local, "page_as_pdf", lambda c, n: b"%PDF")
    monkeypatch.setattr(mineru_cloud, "parse_bytes", lambda c, **kw: None)

    assert "LOCAL" in hybrid_parse.parse(b"%PDF")


def test_no_local_extraction_means_the_cascade_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: None)
    assert hybrid_parse.parse(b"%PDF") is None


# ── block routing: only what benefits from being looked at ───────────────────


def _render(blocks, monkeypatch, *, vision_result=None) -> tuple[str, list]:
    """Render blocks, recording every vision call."""
    from app.services import vision_extract

    seen: list[bytes] = []

    def fake_extract(content, filename):
        seen.append(content)
        return vision_result, None if vision_result else "no output"

    monkeypatch.setattr(vision_extract, "vision_available", lambda: (True, ""))
    monkeypatch.setattr(vision_extract, "extract_image", fake_extract)
    return hybrid_parse._render_blocks(blocks, page_label="p.1"), seen


def test_latex_equations_never_reach_the_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinerU returns equations already in LaTeX. Asking a vision model to
    look at the picture instead replaces an exact answer with a guess."""
    out, seen = _render([_block("equation", text=r"E = mc^2")], monkeypatch)
    assert seen == []
    assert "$$" in out and "E = mc^2" in out


def test_structured_tables_never_reach_the_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, seen = _render(
        [_block("table", html="<table><tr><td>12.5</td></tr></table>",
                caption="Table 1", image=b"PNG")],
        monkeypatch,
    )
    assert seen == []
    assert "<table>" in out and "12.5" in out
    assert "Table 1" in out


def test_a_table_mineru_could_not_render_does_reach_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one table worth a second look: no HTML came back."""
    _, seen = _render([_block("table", html="", image=b"PNGDATA")], monkeypatch)
    assert seen == [b"PNGDATA"]


@pytest.mark.parametrize("kind", ["image", "chart"])
def test_figures_do_reach_the_vision_model(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, seen = _render([_block(kind, image=b"FIGURE")], monkeypatch)
    assert seen == [b"FIGURE"]


def test_page_furniture_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headers and footers repeat on every page; keeping them dilutes every
    chunk they land in."""
    out, _ = _render(
        [_block("header", text="CONFIDENTIAL"),
         _block("text", text="real content"),
         _block("page_number", text="7")],
        monkeypatch,
    )
    assert "real content" in out
    assert "CONFIDENTIAL" not in out and "7" not in out


def test_titles_become_headings_at_their_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headings are what the chunker turns into heading_path."""
    out, _ = _render(
        [_block("title", text="Composition", text_level=2)], monkeypatch
    )
    assert "## Composition" in out


def test_an_unknown_block_type_keeps_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """MinerU may add block types. Dropping one silently is the failure mode
    with no symptom."""
    out, _ = _render([_block("some_future_type", text="still useful")], monkeypatch)
    assert "still useful" in out


# ── figures are never lost, however badly it goes ────────────────────────────


def test_a_figure_survives_when_vision_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, no_vision
) -> None:
    out = hybrid_parse._render_blocks(
        [_block("image", image=b"PNG", caption="Figure 1. Reaction scheme")],
        page_label="p.3",
    )
    assert "Figure 1. Reaction scheme" in out
    assert "p.3" in out


def test_a_figure_survives_when_vision_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, seen = _render([_block("image", image=b"PNG", caption="Fig 2")], monkeypatch)
    assert seen == [b"PNG"]
    assert "Fig 2" in out


def test_a_figure_survives_when_its_bytes_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, seen = _render([_block("image", image=None, caption="Fig 3")], monkeypatch)
    assert seen == []
    assert "Fig 3" in out


def test_vision_output_including_smiles_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chemical-structure path end to end: MinerU gives the picture, the
    vision model gives the SMILES, RDKit's verdict rides along."""
    from app.services.vision_extract import VisionExtraction, VisionMolecule

    extraction = VisionExtraction(
        kind="structure",
        markdown="环氧树脂结构式",
        molecules=[VisionMolecule(smiles="CC(C)(c1ccccc1)", name="DGEBA",
                                  confidence=0.9, verified=True)],
    )
    out, seen = _render(
        [_block("image", image=b"STRUCT", caption="Figure 1")],
        monkeypatch, vision_result=extraction,
    )
    assert seen == [b"STRUCT"]
    assert "CC(C)(c1ccccc1)" in out
    assert "DGEBA" in out
    assert "✓" in out              # the RDKit verification column


# ── scanned documents ────────────────────────────────────────────────────────


def test_a_scan_goes_through_whole_with_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-page triage is pointless when every page would qualify."""
    calls: list[dict] = []
    pages = [_page(n, markdown="", chars=3) for n in (1, 2)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))

    def fake_parse(content, **kw):
        calls.append(kw)
        return mineru_cloud.MinerUDocument(
            blocks=[_block("text", page_idx=0, text="OCR page one"),
                    _block("text", page_idx=1, text="OCR page two")]
        )

    monkeypatch.setattr(mineru_cloud, "parse_bytes", fake_parse)
    output = hybrid_parse.parse(b"%PDF scan")

    assert len(calls) == 1, "one call for the document, not one per page"
    assert calls[0]["ocr"] is True
    assert "OCR page one" in output and "OCR page two" in output
    assert output.count("<!-- page:") == 2


def test_an_oversized_scan_is_refused_rather_than_billed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list = []
    monkeypatch.setattr(get_settings(), "mineru_max_pages_per_doc", 3, raising=False)
    monkeypatch.setattr(
        pdf_local, "extract_pages",
        lambda c, **kw: [_page(n, markdown="", chars=2) for n in range(1, 21)],
    )
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes", lambda c, **kw: sent.append(kw) or None
    )

    hybrid_parse.parse(b"%PDF big scan")
    assert sent == [], "a 20-page scan past a 3-page cap must not be sent"


# ── assembly: downstream still must not change ───────────────────────────────


def test_escalated_pages_keep_their_page_numbers(
    cloud_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [_page(1), _page(2, image_ratio=0.5), _page(3)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)

    from app.services.chunking import chunk_markdown

    chunks = chunk_markdown(hybrid_parse.parse(b"%PDF"))
    assert [c.page_no for c in chunks] == [1, 2, 3]
    assert any("CLOUD TEXT" in c.text for c in chunks)


def test_the_hybrid_tier_routes_through_the_fusion_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tier must call the fusion layer, not the local extractor directly —
    otherwise escalation is wired up but never reached."""
    from app.services import parsing

    monkeypatch.setattr(hybrid_parse, "parse", lambda content: "FUSED OUTPUT")
    result = parsing.parse_document(b"%PDF-1.4", "pdf")
    assert result.parser == "hybrid"
    assert result.markdown == "FUSED OUTPUT"
    assert [name for name, _ in parsing._PDF_TIERS][0] == "hybrid"


# ── shapes the live API actually returns ─────────────────────────────────────


def test_a_text_block_carrying_a_level_becomes_a_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinerU emits headings as {"type": "text", "text_level": 2} and never
    emits a "title" type at all — verified against the live API. Keying the
    heading branch on the type demoted every heading to prose, which cost
    heading_path on every escalated page.
    """
    out, _ = _render(
        [_block("text", text="Epoxy Anticorrosion Primer", text_level=2)], monkeypatch
    )
    assert out.startswith("## Epoxy Anticorrosion Primer")


def test_a_text_block_without_a_level_stays_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, _ = _render([_block("text", text="ordinary body text")], monkeypatch)
    assert not out.lstrip().startswith("#")
    assert "ordinary body text" in out


def test_headings_reach_heading_path_after_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The consequence that matters: a heading MinerU found has to survive all
    the way into the chunk metadata the retriever ranks on."""
    from app.services.chunking import chunk_markdown

    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: [_page(1, image_ratio=0.5)])
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(pdf_local, "page_as_pdf", lambda c, n: b"%PDF")
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes",
        lambda c, **kw: mineru_cloud.MinerUDocument(blocks=[
            _block("text", text="Composition", text_level=2),
            _block("text", text="Zinc phosphate 12.5 wt%"),
        ]),
    )

    chunks = chunk_markdown(hybrid_parse.parse(b"%PDF"))
    assert any("Composition" in c.heading_path for c in chunks)


def test_a_provider_error_does_not_reach_the_knowledge_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider errors carry account identifiers. A real 429 read "Your
    account org-a752… <ak-fbkd…> is suspended", and whatever this function
    returns becomes an indexed, searchable chunk — so the reason belongs in
    the log, not the document."""
    from app.services import vision_extract

    leaky = (
        "Error code: 429 - {'error': {'message': 'Your account "
        "org-a752e5b8ef89448884320309f69c90d4 <ak-fbkdnhefxa7i11f4b541> is suspended'}}"
    )
    monkeypatch.setattr(vision_extract, "vision_available", lambda: (True, ""))
    monkeypatch.setattr(vision_extract, "extract_image", lambda c, f: (None, leaky))

    out = hybrid_parse._render_blocks(
        [_block("image", image=b"PNG", caption="Figure 1")], page_label="p.3"
    )
    assert "Figure 1" in out                 # the figure is still not lost
    assert "org-" not in out                 # but the account id is gone
    assert "ak-" not in out
    assert len(out) < 200                    # and so is the wall of noise


# ── cost control: a broken connection must not be paid for once per page ─────


def _failing_cloud(monkeypatch: pytest.MonkeyPatch, results: list) -> list[dict]:
    """MinerU reachable but answering with *results* in order; records calls."""
    calls: list[dict] = []
    outcomes = iter(results)

    def fake_parse(content, **kw):
        calls.append(kw)
        try:
            ok = next(outcomes)
        except StopIteration:
            ok = False
        if not ok:
            return None
        return mineru_cloud.MinerUDocument(blocks=[_block("text", text="CLOUD TEXT")])

    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(pdf_local, "page_as_pdf", lambda c, n: b"%PDF one page")
    monkeypatch.setattr(mineru_cloud, "parse_bytes", fake_parse)
    return calls


def test_escalation_gives_up_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten candidate pages, an unreachable MinerU, three attempts — not ten.

    Escalation is a serial loop and each failure costs the full per-page
    timeout, so without a breaker one broken network is billed once per page.
    That is how a document can spend twenty timeouts learning the same fact.
    """
    monkeypatch.setattr(get_settings(), "mineru_max_page_failures", 3, raising=False)
    pages = [_page(n, image_ratio=0.5) for n in range(1, 11)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)
    calls = _failing_cloud(monkeypatch, [False] * 10)

    output = hybrid_parse.parse(b"%PDF ten figures")

    assert len(calls) == 3, "the breaker must stop the loop, not just log it"
    # Nothing is lost: every page keeps the text local extraction produced.
    assert output.count("<!-- page:") == 10


def test_isolated_failures_do_not_trip_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A working connection with a few hard pages must run to the end.

    The counter is consecutive on purpose — counting total failures would
    abandon a document that is mostly succeeding.
    """
    monkeypatch.setattr(get_settings(), "mineru_max_page_failures", 3, raising=False)
    pages = [_page(n, image_ratio=0.5) for n in range(1, 8)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)
    calls = _failing_cloud(
        monkeypatch, [False, False, True, False, False, True, False]
    )

    hybrid_parse.parse(b"%PDF mixed")

    assert len(calls) == 7, "two failures then a success must reset the counter"


def test_a_page_is_escalated_on_the_short_per_page_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-page escalation must not inherit the whole-document timeout.

    Twenty pages on the document budget is 6000 s of sequential waiting for a
    connection that is not going to work.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_timeout_s", 300.0, raising=False)
    monkeypatch.setattr(settings, "mineru_page_timeout_s", 90.0, raising=False)
    monkeypatch.setattr(
        pdf_local, "extract_pages", lambda c, **kw: [_page(1, image_ratio=0.5)]
    )
    calls = _failing_cloud(monkeypatch, [True])

    hybrid_parse.parse(b"%PDF one figure")

    assert calls and calls[0]["timeout"] == 90.0


# ── scans must survive turning the blanket OCR off ───────────────────────────


def _scan_pages() -> list:
    """Four pages with no text layer — what `looks_scanned` is meant to catch."""
    return [_page(n, markdown="", chars=3) for n in range(1, 5)]


def test_a_scan_falls_back_to_the_layout_parser_ocr_when_rapidocr_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The host in question: Tesseract present, RapidOCR missing, MinerU off.

    Blanket OCR is disabled, so local extraction correctly yields nothing for a
    scan. Without this fallback the document is silently dropped — the one
    failure mode with no symptom.
    """
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: _scan_pages())
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (False, "未启用"))

    from app.services import rapidocr_local

    monkeypatch.setattr(rapidocr_local, "ocr_pdf", lambda c: None)
    monkeypatch.setattr(pdf_local, "ocr_markdown", lambda c, **kw: "OCR RESCUED TEXT")

    assert hybrid_parse.parse(b"%PDF scan") == "OCR RESCUED TEXT"


def test_rapidocr_is_preferred_over_the_layout_parser_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It ships its weights in the wheel and reports what it did; try it first."""
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: _scan_pages())
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (False, "未启用"))

    from app.services import rapidocr_local

    called: list[str] = []
    monkeypatch.setattr(
        rapidocr_local, "ocr_pdf", lambda c: called.append("rapid") or "RAPID TEXT"
    )
    monkeypatch.setattr(
        pdf_local, "ocr_markdown",
        lambda c, **kw: called.append("layout") or "LAYOUT TEXT",
    )

    assert hybrid_parse.parse(b"%PDF scan") == "RAPID TEXT"
    assert called == ["rapid"], "the backstop must not run once the first tier worked"


def test_a_scan_is_not_lost_when_the_cloud_parser_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured-but-unreachable MinerU must not be worse than no MinerU.

    `local_only` is empty for a scan, so returning it would hand the cascade an
    empty document instead of a readable one.
    """
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: _scan_pages())
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(mineru_cloud, "parse_bytes", lambda c, **kw: None)  # timeout

    from app.services import rapidocr_local

    monkeypatch.setattr(rapidocr_local, "ocr_pdf", lambda c: "RAPID TEXT")

    assert hybrid_parse.parse(b"%PDF scan") == "RAPID TEXT"


# ── mixed documents: some pages scanned, some not ────────────────────────────


def test_a_page_with_no_text_layer_is_escalated() -> None:
    """`looks_scanned` is `all()` over pages, so a mixed file never reaches the
    scanned branch. Escalation is the only route its blank pages have."""
    assert hybrid_parse._needs_escalation(_page(1, markdown="", chars=3)) is True


def test_a_mixed_document_escalates_only_the_pages_with_no_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The good pages must still not cost quota."""
    pages = [
        _page(1),                              # ordinary prose
        _page(2, markdown="", chars=2),        # a scan
        _page(3, markdown="", chars=2),        # a scan
        _page(4),                              # ordinary prose
    ]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)
    sent: list[int] = []
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(
        pdf_local, "page_as_pdf",
        lambda content, page_no: sent.append(page_no) or b"%PDF one page",
    )
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes",
        lambda content, **kw: mineru_cloud.MinerUDocument(
            blocks=[_block("text", text="RECOVERED")]
        ),
    )

    output = hybrid_parse.parse(b"%PDF mixed")

    assert sent == [2, 3], "only the text-layer-less pages"
    assert output.count("RECOVERED") == 2


def test_a_mixed_document_with_no_cloud_parser_reports_what_it_could_not_read(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """Half an empty document must not ship silently.

    The pages are unreadable either way; the difference is whether an operator
    is given a number they can act on.
    """
    import logging

    pages = [_page(1), _page(2, markdown="", chars=2), _page(3, markdown="", chars=2)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: pages)
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (False, "未启用"))

    with caplog.at_level(logging.WARNING):
        output = hybrid_parse.parse(b"%PDF mixed")

    assert "2/3 pages have no text layer" in caplog.text
    assert output, "the readable page is still returned"
