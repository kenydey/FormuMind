"""Local PDF extraction and the triage signals that drive cloud escalation.

Built against real PDFs rather than fixtures: the whole point of this layer is
what PyMuPDF observes in a document, and a hand-written stub would only assert
that the stub is consistent with itself.

⚠️ These tests also pin a correction. pymupdf4llm's `page_chunks=True` returns
only ``metadata`` / ``text`` / ``toc_items`` / ``page_boxes`` — it does **not**
report per-page tables or images, despite write-ups that say otherwise. The
counts come from PyMuPDF directly, and `test_page_signals_come_from_pymupdf`
fails loudly if that is ever quietly reverted.
"""
from __future__ import annotations

import pytest

from app.services import pdf_local

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("pymupdf4llm")


@pytest.fixture(scope="module")
def three_page_pdf() -> bytes:
    """Page 1 plain prose, page 2 a ruled table, page 3 a large figure.

    One page of each kind the router has to tell apart.
    """
    doc = pymupdf.open()

    page = doc.new_page()
    page.insert_text((72, 90), "Epoxy Anticorrosion Primer", fontsize=20)
    page.insert_text((72, 130), "This formulation targets 1000 h salt spray per ASTM B117.", fontsize=11)
    page.insert_text((72, 150), "VOC content is held below 250 g/L by a waterborne carrier.", fontsize=11)

    table_page = doc.new_page()
    table_page.insert_text((72, 90), "Table 1. Composition", fontsize=14)
    rows = [
        ("Component", "Role", "wt%"),
        ("Bisphenol-A epoxy", "resin", "38.0"),
        ("Polyamide hardener", "hardener", "14.0"),
        ("Zinc phosphate", "inhibitor", "12.5"),
    ]
    y = 120
    for row in rows:
        x = 72
        for width, cell in zip((180, 120, 60), row):
            table_page.draw_rect(pymupdf.Rect(x, y, x + width, y + 22))
            table_page.insert_text((x + 4, y + 15), cell, fontsize=10)
            x += width
        y += 22

    figure_page = doc.new_page()
    figure_page.insert_text((72, 90), "Figure 1. Reaction scheme", fontsize=14)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
    pixmap.set_rect(pixmap.irect, (200, 220, 255))
    figure_page.insert_image(pymupdf.Rect(72, 110, 472, 410), pixmap=pixmap)

    return doc.tobytes()


# ── extraction ───────────────────────────────────────────────────────────────


def test_reports_available_when_installed() -> None:
    ok, hint = pdf_local.local_available()
    assert ok is True
    assert hint == ""


def test_missing_library_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """It has to behave as a parser tier: unavailable means None, so the
    cascade moves on rather than the upload failing."""
    monkeypatch.setattr(pdf_local, "optional_import", lambda mod: False)
    ok, hint = pdf_local.local_available()
    assert ok is False
    assert "pymupdf4llm" in hint
    assert pdf_local.extract_pages(b"%PDF-1.4") is None
    assert pdf_local.to_markdown(b"%PDF-1.4") is None


def test_extracts_one_entry_per_page(three_page_pdf: bytes) -> None:
    pages = pdf_local.extract_pages(three_page_pdf)
    assert pages is not None
    assert [p.page_no for p in pages] == [1, 2, 3]   # 1-based, matching markers


def test_table_is_rendered_as_markdown(three_page_pdf: bytes) -> None:
    """A ruled table needs no cloud parser — which is exactly why the router
    must be able to tell that the local pass already handled it."""
    pages = pdf_local.extract_pages(three_page_pdf)
    table_page = pages[1]
    assert table_page.has_markdown_table
    assert "Zinc phosphate" in table_page.markdown
    assert "12.5" in table_page.markdown


def test_corrupt_input_degrades_to_none() -> None:
    assert pdf_local.extract_pages(b"not a pdf at all") is None


# ── triage signals ───────────────────────────────────────────────────────────


def test_page_signals_come_from_pymupdf(three_page_pdf: bytes) -> None:
    """Text page: nothing to escalate. Table page: a table. Figure page: an
    image covering a real fraction of the sheet."""
    prose, table, figure = pdf_local.extract_pages(three_page_pdf)

    assert (prose.n_tables, prose.n_images) == (0, 0)
    assert prose.image_area_ratio == 0.0

    assert table.n_tables >= 1

    assert figure.n_images >= 1
    assert figure.image_area_ratio > 0.1


def test_figures_are_detected_not_just_tables(three_page_pdf: bytes) -> None:
    """Routing on tables alone would miss chemical structure diagrams and
    reaction schemes entirely, because those are figures."""
    figure = pdf_local.extract_pages(three_page_pdf)[2]
    assert figure.n_tables == 0        # no table here at all
    assert figure.n_images >= 1        # but there is something worth looking at


def test_a_text_page_is_not_mistaken_for_a_scan(three_page_pdf: bytes) -> None:
    assert pdf_local.extract_pages(three_page_pdf)[0].looks_scanned is False


def test_document_wide_scan_detection_tolerates_one_bare_page(
    three_page_pdf: bytes,
) -> None:
    """The figure page has almost no text, but the document is not a scan. A
    per-page verdict would send a perfectly good PDF to OCR."""
    pages = pdf_local.extract_pages(three_page_pdf)
    assert pages[2].looks_scanned is True      # that one page, yes
    assert pdf_local.looks_scanned(pages) is False   # the document, no


def test_empty_page_list_counts_as_scanned() -> None:
    assert pdf_local.looks_scanned([]) is True


# ── page slicing (what makes vision routing possible at all) ─────────────────


def test_page_as_pdf_keeps_the_text_layer(three_page_pdf: bytes) -> None:
    """The reason to slice rather than rasterise. A 150 dpi render throws away
    characters that were already perfectly available, and costs the same one
    page of quota downstream."""
    sliced = pdf_local.page_as_pdf(three_page_pdf, 2)
    assert sliced

    reopened = pymupdf.open(stream=sliced, filetype="pdf")
    assert reopened.page_count == 1
    assert "Zinc phosphate" in reopened[0].get_text()


def test_page_as_png_renders(three_page_pdf: bytes) -> None:
    png = pdf_local.page_as_png(three_page_pdf, 3, dpi=72)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_page_numbers_are_validated(three_page_pdf: bytes) -> None:
    for page_no in (0, 4, -1):
        assert pdf_local.page_as_pdf(three_page_pdf, page_no) is None
        assert pdf_local.page_as_png(three_page_pdf, page_no) is None


def test_embedded_images_are_reachable(three_page_pdf: bytes) -> None:
    """Before this module nothing in the repo could reach a picture inside a
    PDF, so 'send the figures to the vision model' had no figure to send."""
    images = pdf_local.embedded_images(three_page_pdf, 3)
    assert images and isinstance(images[0], bytes)
    assert pdf_local.embedded_images(three_page_pdf, 1) == []


# ── assembly: downstream must not need to change ─────────────────────────────


def test_markers_let_the_existing_chunker_recover_page_numbers(
    three_page_pdf: bytes,
) -> None:
    """The whole integration rests on this. Emitting `<!-- page:N -->` means
    chunk_markdown keeps producing page_no and heading_path with no change to
    chunking, kb_index or chunk_store."""
    from app.services.chunking import chunk_markdown

    markdown = pdf_local.to_markdown(three_page_pdf)
    assert markdown

    chunks = chunk_markdown(markdown)
    assert [c.page_no for c in chunks] == [1, 2, 3]
    assert "Epoxy Anticorrosion Primer" in chunks[0].heading_path
    assert "<!-- page:" not in " ".join(c.text for c in chunks)   # markers stripped


def test_assemble_skips_blank_pages() -> None:
    assembled = pdf_local.assemble([(1, "first"), (2, "   "), (3, "third")])
    assert "<!-- page:1 -->" in assembled
    assert "<!-- page:2 -->" not in assembled
    assert "<!-- page:3 -->" in assembled


def test_hybrid_is_the_first_pdf_tier() -> None:
    """Fastest and, on a CPU-only host, the best available: docling and marker
    need weights (marker ~54 s/page on CPU) and local MinerU needs a GPU."""
    from app.services import parsing

    assert [name for name, _ in parsing._PDF_TIERS][0] == "hybrid"


def test_parse_document_routes_pdfs_through_hybrid(three_page_pdf: bytes) -> None:
    from app.services.parsing import parse_document

    result = parse_document(three_page_pdf, "pdf")
    assert result.parser == "hybrid"
    assert "Zinc phosphate" in result.markdown


# ── layout analysis: the memory/correctness trade-off ────────────────────────


@pytest.fixture(scope="module")
def two_column_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 70), "Two-Column Paper", fontsize=18)
    left = ["Left column line one about", "zinc phosphate inhibitors and", "their salt spray behaviour."]
    right = ["Right column line one about", "epoxy amine cure kinetics at", "elevated temperature."]
    y = 110
    for l, r in zip(left, right):
        page.insert_text((60, y), l, fontsize=10)
        page.insert_text((320, y), r, fontsize=10)
        y += 16
    return doc.tobytes()


def test_layout_analysis_defaults_on() -> None:
    """Off is 25x faster and half the memory, and on a single-column page the
    output is identical — but it interleaves two-column pages, and papers and
    patents are two-column. Correctness wins by default."""
    from app.config import get_settings

    assert get_settings().pdf_layout_analysis is True


def test_two_column_reading_order_is_preserved(
    two_column_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The concrete reason for that default: without layout analysis the two
    columns are read line-by-line across the page, so unrelated sentences merge
    into one chunk and one embedding."""
    monkeypatch.setattr(pdf_local, "_layout_configured", False)
    markdown = pdf_local.to_markdown(two_column_pdf)
    assert markdown

    left_end = markdown.find("salt spray behaviour")
    right_start = markdown.find("Right column line one")
    assert left_end != -1 and right_start != -1
    assert left_end < right_start, (
        "columns interleaved — the left column must be read out before the right"
    )


def test_layout_switch_is_applied_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """use_layout tears down and rebuilds module globals, so it must not run
    per document."""
    calls: list[bool] = []
    import pymupdf4llm

    monkeypatch.setattr(pdf_local, "_layout_configured", False)
    monkeypatch.setattr(pymupdf4llm, "_use_layout", None, raising=False)
    monkeypatch.setattr(pymupdf4llm, "use_layout", lambda yes: calls.append(yes))

    pdf_local._configure_layout()
    pdf_local._configure_layout()
    pdf_local._configure_layout()
    assert calls == [True]


# ── vision availability must not over-promise ────────────────────────────────


@pytest.fixture(autouse=True)
def _no_runtime_overlay():
    """Clear the runtime-secret overlay around every test in this module.

    These tests monkeypatch attributes on the cached ``Settings`` object, but the
    overlay *wins* over ``Settings`` (``runtime_secrets.effective``). Any earlier
    test that left `llm_provider` in the overlay — `test_v03.py` does — silently
    overrode the monkeypatch and made these fail depending on collection order.
    """
    from app.services.runtime_secrets import get_runtime_secrets

    rs = get_runtime_secrets()
    rs.clear()
    yield
    rs.clear()


def test_vision_reports_unavailable_without_its_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This returned (True, "") on a machine with no `openai` package, so
    callers were told vision worked and every image came back "No module named
    'openai'" — the same "installed but cannot work" gap that let uploads index
    nothing. A probe that answers yes when the next call cannot succeed is
    worse than no probe.
    """
    from app.config import get_settings
    from app.services import errors, vision_extract

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "moonshot", raising=False)
    monkeypatch.setattr(settings, "moonshot_api_key", "sk-test", raising=False)
    monkeypatch.setattr(errors, "optional_import", lambda mod: False)

    ok, hint = vision_extract.vision_available()
    assert ok is False
    assert "openai" in hint


def test_anthropic_needs_its_own_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.services import errors, vision_extract

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "anthropic", raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test", raising=False)
    monkeypatch.setattr(errors, "optional_import", lambda mod: mod == "openai")

    ok, hint = vision_extract.vision_available()
    assert ok is False
    assert "anthropic" in hint


def test_a_provider_without_image_support_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek is OpenAI-compatible for chat and rejects images outright:
    an image_url content part comes back as "unknown variant 'image_url',
    expected 'text'". Being compatible for chat says nothing about images, and
    without this the UI called vision available and every figure failed on a
    400 naming a JSON deserialisation error rather than the real cause.
    """
    from app.config import get_settings
    from app.services import vision_extract

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "deepseek", raising=False)
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test", raising=False)

    ok, hint = vision_extract.vision_available()
    assert ok is False
    assert "deepseek" in hint
    assert "Claude" in hint or "OpenAI" in hint     # says what to switch to


def test_a_vision_capable_provider_is_still_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The block list is what has been checked, not a guess at every
    provider — vision-capable ones must keep working."""
    from app.config import get_settings
    from app.services import vision_extract

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    assert vision_extract.vision_available() == (True, "")


def test_vision_role_rescues_a_text_provider_that_cannot_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of separating the roles.

    DeepSeek for text is a perfectly reasonable choice, and before roles existed
    it took the chemical-figure path down with it. Naming a vision provider must
    make vision available again *without* touching the text configuration.
    """
    from app.config import get_settings
    from app.services import vision_extract

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "deepseek", raising=False)
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-text", raising=False)
    # Same state that used to report unavailable:
    assert vision_extract.vision_available()[0] is False

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "gpt-4o", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-vision", raising=False)
    assert vision_extract.vision_available() == (True, "")


def test_custom_endpoint_is_reported_configured_not_capable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rented endpoint runs weights we cannot inspect.

    Blocking it would break a working deployment; claiming it can see would be
    the fifth repeat of the lying-probe bug. So it reports *configured* and is
    left to fail at the call with the provider's own message — the policy
    already documented for qwen/moonshot.
    """
    from app.config import get_settings
    from app.services import vision_extract

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "vision_provider", "custom", raising=False)
    monkeypatch.setattr(settings, "vision_model", "tgi", raising=False)
    monkeypatch.setattr(settings, "vision_api_key", "hf_token", raising=False)
    monkeypatch.setattr(
        settings, "vision_base_url", "https://x.endpoints.huggingface.cloud/v1", raising=False
    )

    ok, hint = vision_extract.vision_available()
    assert ok is True
    # "Configured" is all it may assert — no capability claim in the hint.
    assert hint == ""


def test_vision_unavailable_without_a_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endpoint with no model name cannot be called; say so up front rather
    than sending an empty `model` and reading back a vendor parse error."""
    from app.config import get_settings
    from app.services import vision_extract

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "vision_provider", "custom", raising=False)
    monkeypatch.setattr(settings, "vision_model", "", raising=False)
    monkeypatch.setattr(settings, "vision_api_key", "hf_token", raising=False)

    ok, hint = vision_extract.vision_available()
    assert ok is False
    assert "模型" in hint


def test_disabling_vision_via_the_overlay_takes_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env-flags panel writes this switch through the runtime overlay, and
    the gate used to read it straight off Settings — so turning it off in the UI
    did nothing until restart."""
    from app.config import get_settings
    from app.services import vision_extract
    from app.services.runtime_secrets import get_runtime_secrets

    settings = get_settings()
    monkeypatch.setattr(settings, "vision_extract_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    assert vision_extract.vision_available()[0] is True

    get_runtime_secrets().set("vision_extract_enabled", False)
    ok, hint = vision_extract.vision_available()
    assert ok is False
    assert "禁用" in hint


# ── the OCR pymupdf4llm runs on its own ──────────────────────────────────────


def _captured_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Intercept the real `to_markdown` and report what it was called with.

    Asserted at the library boundary rather than by reading our own source: the
    defect being pinned is a *default inside pymupdf4llm* (`use_ocr=True` in its
    layout parser), and only the actual call shows whether we override it.
    """
    import pymupdf4llm

    seen: dict = {}
    real = pymupdf4llm.to_markdown

    def spy(doc, **kwargs):
        seen.update(kwargs)
        return real(doc, **kwargs)

    monkeypatch.setattr(pymupdf4llm, "to_markdown", spy)
    return seen


def test_local_extraction_does_not_run_its_own_ocr(
    three_page_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pymupdf4llm's layout parser OCRs every text-poor page unless told not to.

    It is the first tier of the cascade and runs on every PDF, so with an OCR
    engine on PATH that cost is paid for the whole corpus before any other tier
    is consulted — measured at ~50 s of a 51.7 s parse. Scans are not abandoned
    by turning it off; `looks_scanned` hands those to the tiers built for them.
    """
    monkeypatch.setattr(pdf_local.get_settings(), "pdf_local_ocr", False, raising=False)
    seen = _captured_kwargs(monkeypatch)

    assert pdf_local.extract_pages(three_page_pdf) is not None
    assert seen.get("use_ocr") is False


def test_the_ocr_pass_can_still_be_turned_back_on(
    three_page_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pdf_local.get_settings(), "pdf_local_ocr", True, raising=False)
    seen = _captured_kwargs(monkeypatch)

    assert pdf_local.extract_pages(three_page_pdf) is not None
    assert seen.get("use_ocr") is True


def test_legacy_mode_is_not_handed_an_argument_it_would_reject(
    three_page_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`use_ocr` belongs to the layout parser only.

    The legacy path prints "arguments ignored in legacy mode" for anything it
    does not recognise and then carries on — so passing it there would produce
    a warning per document while changing nothing.
    """
    monkeypatch.setattr(
        pdf_local.get_settings(), "pdf_layout_analysis", False, raising=False
    )
    assert "use_ocr" not in pdf_local._markdown_kwargs()


def test_deliberate_ocr_reports_that_legacy_mode_cannot_do_it(
    three_page_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`use_ocr` is a layout-parser argument; legacy mode drops it silently.

    Without this the scanned-document backstop would run a full parse, get the
    same empty result it already had, and give no reason why.
    """
    monkeypatch.setattr(
        pdf_local.get_settings(), "pdf_layout_analysis", False, raising=False
    )
    assert pdf_local.ocr_markdown(three_page_pdf) is None


def test_deliberate_ocr_overrides_the_off_by_default_setting(
    three_page_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setting governs *every* document; this call is about one that needs it."""
    settings = pdf_local.get_settings()
    monkeypatch.setattr(settings, "pdf_layout_analysis", True, raising=False)
    monkeypatch.setattr(settings, "pdf_local_ocr", False, raising=False)
    seen = _captured_kwargs(monkeypatch)

    pdf_local.ocr_markdown(three_page_pdf)
    assert seen.get("use_ocr") is True


def test_deliberate_ocr_refuses_a_document_past_the_page_cap(
    three_page_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One long scan must not be able to consume a whole ingest run."""
    assert pdf_local.ocr_markdown(three_page_pdf, max_pages=2) is None
    assert pdf_local.ocr_markdown(three_page_pdf, max_pages=3) is not None
