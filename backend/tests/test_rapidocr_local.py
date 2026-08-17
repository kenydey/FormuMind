"""Local OCR for scanned PDFs.

The behaviours worth pinning are the ones that decide whether a scanned document
is readable at all, and the two post-processing steps that are easy to lose:

* reading order — RapidOCR returns a flat list of boxes with no notion of lines,
  so anything visually on one row arrives as several entries. Without grouping,
  a two-column page comes out interleaved.
* fullwidth normalisation — OCR of Chinese returns ``磷酸锌１5．０份`` for
  ``磷酸锌 15.0 份`` and ``Ｅ-44`` for the resin grade ``E-44``. On a formulation
  platform those characters *are* the data.

The engine itself is faked. A real OCR pass costs seconds and the models are 16 MB
of weights; what needs testing is our code around it.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.config import get_settings
from app.services import rapidocr_local as ro


@pytest.fixture(autouse=True)
def _fresh():
    ro._ENGINE = None
    get_settings.cache_clear()
    yield
    ro._ENGINE = None
    get_settings.cache_clear()


def _box(x0: float, y0: float, x1: float, y1: float):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _make_output(triples):
    """Fold (box, text, score) triples into the RapidOCROutput object shape."""
    class Out:
        boxes = [t[0] for t in triples]
        txts = tuple(t[1] for t in triples)
        scores = tuple(t[2] for t in triples)

    return Out()


def _fake_engine(monkeypatch, result, *, record: dict | None = None):
    """Inject a fake rapidocr module whose engine returns `result`."""
    mod = types.ModuleType("rapidocr")

    class RapidOCR:
        def __init__(self, **kwargs):
            if record is not None:
                record.update(kwargs)

        def __call__(self, img):
            return _make_output(result)

    mod.RapidOCR = RapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", mod)
    monkeypatch.setattr(ro, "optional_import", lambda name: name == "rapidocr")
    return mod


# ── availability ─────────────────────────────────────────────────────────────


def test_unavailable_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", False, raising=False)
    ok, hint = ro.rapidocr_available()
    assert ok is False
    assert "FORMUMIND_RAPIDOCR_ENABLED" in hint


def test_unavailable_without_the_engine(monkeypatch):
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", True, raising=False)
    monkeypatch.setattr(ro, "optional_import", lambda name: False)
    ok, hint = ro.rapidocr_available()
    assert ok is False
    # Actionable: names the package and where to click.
    assert "rapidocr" in hint and "依赖管理" in hint


def test_available_with_the_engine(monkeypatch):
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", True, raising=False)
    monkeypatch.setattr(ro, "optional_import", lambda name: True)
    assert ro.rapidocr_available() == (True, "")


# ── reading order ────────────────────────────────────────────────────────────


def test_fragments_on_one_row_join_into_one_line():
    """Detection splits a row into several boxes; joining them is what keeps a
    sentence from becoming three."""
    result = [
        (_box(300, 100, 400, 130), "份", 0.9),
        (_box(60, 102, 290, 132), "磷酸锌 15.0", 0.9),
    ]
    assert ro._to_reading_order(result) == "磷酸锌 15.0份"


def test_rows_are_ordered_top_to_bottom():
    result = [
        (_box(60, 300, 400, 330), "第三行", 0.9),
        (_box(60, 100, 400, 130), "第一行", 0.9),
        (_box(60, 200, 400, 230), "第二行", 0.9),
    ]
    assert ro._to_reading_order(result).splitlines() == ["第一行", "第二行", "第三行"]


def test_two_columns_do_not_interleave():
    """The failure this grouping prevents: reading across the gutter."""
    result = [
        (_box(60, 100, 300, 130), "左一", 0.9),
        (_box(400, 100, 640, 130), "右一", 0.9),
        (_box(60, 200, 300, 230), "左二", 0.9),
        (_box(400, 200, 640, 230), "右二", 0.9),
    ]
    lines = ro._to_reading_order(result).splitlines()
    assert lines == ["左一右一", "左二右二"]


def test_latin_fragments_keep_their_space():
    result = [
        (_box(60, 100, 200, 130), "salt", 0.9),
        (_box(210, 100, 340, 130), "spray", 0.9),
    ]
    assert ro._to_reading_order(result) == "salt spray"


def test_blank_and_empty_results_are_safe():
    assert ro._to_reading_order([]) == ""
    assert ro._to_reading_order(None) == ""
    assert ro._to_reading_order([(_box(0, 0, 1, 1), "   ", 0.9)]) == ""


# ── fullwidth normalisation ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("磷酸锌１5．０份", "磷酸锌15.0份"),
        ("环氧树脂Ｅ－44", "环氧树脂E-44"),
        ("固化剂２5份", "固化剂25份"),
        ("ｐＨ值８．5", "pH值8.5"),
        ("盐雾１２００小时", "盐雾1200小时"),
    ],
)
def test_fullwidth_digits_and_latin_are_normalised(raw, expected):
    assert raw.translate(ro._FULLWIDTH) == expected


def test_cjk_punctuation_is_left_alone():
    """Normalisation is about numbers and identifiers, not rewriting prose."""
    text = "无起泡、无锈蚀。「实施例」"
    assert text.translate(ro._FULLWIDTH) == text


def _real_png() -> bytes:
    """A decodable PNG — cv2.imdecode rightly rejects made-up bytes.

    cv2 arrives with the ``parse_pro`` extra, not the offline baseline, so this
    skips rather than errors when it is absent. Every test that needs a real
    image goes through here, which makes this the one place to guard.
    """
    cv2 = pytest.importorskip("cv2", reason="opencv-python-headless is in the parse_pro extra")
    np = pytest.importorskip("numpy")

    ok, buf = cv2.imencode(".png", np.full((40, 120, 3), 255, np.uint8))
    assert ok
    return buf.tobytes()


def test_ocr_png_normalises_its_output(monkeypatch):
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", True, raising=False)
    monkeypatch.setattr(ro, "optional_import", lambda name: True)
    monkeypatch.setattr(ro, "_engine", lambda: _StubEngine("磷酸锌１5．０份"))
    assert ro.ocr_png(_real_png()) == "磷酸锌15.0份"


def test_undecodable_bytes_return_none(monkeypatch):
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", True, raising=False)
    monkeypatch.setattr(ro, "optional_import", lambda name: True)
    monkeypatch.setattr(ro, "_engine", lambda: _StubEngine("x"))
    assert ro.ocr_png(b"not an image at all") is None


class _StubEngine:
    def __init__(self, text: str):
        self._text = text

    def __call__(self, img):
        return _make_output([(_box(60, 100, 400, 130), self._text, 0.9)])


# ── engine construction ──────────────────────────────────────────────────────


def test_thread_count_is_pinned(monkeypatch):
    """ONNX Runtime defaults to every core, which starves the web workers it
    shares a 4-core box with. Thread count does not change peak memory, only
    latency, so this is purely about not monopolising the CPU."""
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "rapidocr_threads", 2, raising=False)
    seen: dict = {}
    _fake_engine(monkeypatch, [], record=seen)
    ro._engine()
    assert seen.get("params") == {"EngineConfig.onnxruntime.intra_op_num_threads": 2}


def test_engine_is_built_once(monkeypatch):
    """~1.2 s and ~125 MB per construction: N concurrent uploads must not mean
    N copies of three ONNX sessions."""
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", True, raising=False)
    built: list[int] = []
    mod = types.ModuleType("rapidocr")

    class RapidOCR:
        def __init__(self, **kw):
            built.append(1)

        def __call__(self, img):
            return _make_output([])

    mod.RapidOCR = RapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", mod)
    monkeypatch.setattr(ro, "optional_import", lambda name: True)

    for _ in range(3):
        ro._engine()
    assert len(built) == 1


def test_engine_failure_degrades_to_none(monkeypatch):
    """OCR is one tier of a cascade; a broken engine must fall through, not
    abort an ingest."""
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", True, raising=False)
    mod = types.ModuleType("rapidocr")

    class RapidOCR:
        def __init__(self, **kw):
            raise RuntimeError("onnxruntime missing a provider")

    mod.RapidOCR = RapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", mod)
    monkeypatch.setattr(ro, "optional_import", lambda name: True)

    assert ro._engine() is None
    assert ro.ocr_png(_real_png()) is None


def test_ocr_png_declines_when_unavailable(monkeypatch):
    monkeypatch.setattr(get_settings(), "rapidocr_enabled", False, raising=False)
    assert ro.ocr_png(_real_png()) is None


# ── the gap this closes ──────────────────────────────────────────────────────


def test_a_scan_is_readable_without_any_cloud_parser(monkeypatch):
    """The whole point.

    A scan has no text layer, so every text-layer tier returns nothing. Before
    local OCR, `hybrid_parse` saw MinerU unavailable and returned the empty local
    result, the rest of the cascade also gave up, and the document became a
    "可能是扫描件" placeholder with nothing indexed.
    """
    from app.services import hybrid_parse, mineru_cloud, pdf_local

    # The real dataclass, not a look-alike: `LocalPage` carries a `looks_scanned`
    # property that the routing reads, and a SimpleNamespace with the same fields
    # silently lacks it.
    page = pdf_local.LocalPage(
        page_no=1, markdown="", char_count=0, n_tables=0, n_images=1,
        image_area_ratio=0.9, has_markdown_table=False,
    )
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: [page])
    monkeypatch.setattr(pdf_local, "looks_scanned", lambda pages: True)
    monkeypatch.setattr(pdf_local, "assemble", lambda parts: "")
    # No cloud parser configured — the situation that used to lose the document.
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (False, "未配置 Token"))
    monkeypatch.setattr(ro, "ocr_pdf", lambda content: "<!-- page:1 -->\n\n扫描件正文")

    assert hybrid_parse.parse(b"%PDF fake") == "<!-- page:1 -->\n\n扫描件正文"


def test_a_text_pdf_never_pays_for_ocr(monkeypatch):
    """OCR is seconds per page. A document with a text layer must not touch it."""
    from app.services import hybrid_parse, mineru_cloud, pdf_local

    called: list[int] = []
    page = pdf_local.LocalPage(
        page_no=1, markdown="正文", char_count=900, n_tables=0, n_images=0,
        image_area_ratio=0.0, has_markdown_table=False,
    )
    monkeypatch.setattr(pdf_local, "extract_pages", lambda c, **kw: [page])
    monkeypatch.setattr(pdf_local, "looks_scanned", lambda pages: False)
    monkeypatch.setattr(pdf_local, "assemble", lambda parts: "正文")
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (False, "未配置"))
    monkeypatch.setattr(ro, "ocr_pdf", lambda content: called.append(1) or "OCR")

    assert hybrid_parse.parse(b"%PDF fake") == "正文"
    assert called == []
