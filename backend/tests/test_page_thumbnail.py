"""Phase 3 — page thumbnails + VLM chart fallback (opt-in)."""
import fitz  # PyMuPDF

from app.config import get_settings
from app.services import page_thumbnails as pt


def _tiny_pdf(pages: int = 2) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"page {i + 1}")
    return doc.tobytes()


def test_defaults_off():
    s = get_settings()
    assert s.page_thumbnail_enabled is False
    assert s.vlm_fallback_enabled is False
    assert s.page_thumbnail_dpi == 40
    assert s.vlm_max_pages == 4


def test_render_page_thumbnails():
    thumbs = pt.render_page_thumbnails(_tiny_pdf(2), dpi=40)
    assert len(thumbs) == 2
    assert thumbs[0].page == 1
    assert thumbs[0].png[:8] == b"\x89PNG\r\n\x1a\n"
    assert thumbs[0].width > 0 and thumbs[0].height > 0


def test_render_garbage_fail_open():
    assert pt.render_page_thumbnails(b"not a pdf", dpi=40) == []


def test_maybe_store_disabled_zero_overhead(monkeypatch):
    monkeypatch.setattr(get_settings(), "page_thumbnail_enabled", False, raising=False)

    def _boom(*a, **k):
        raise AssertionError("renderer must not run when disabled")

    monkeypatch.setattr(pt, "render_page_thumbnails", _boom)
    assert pt.maybe_store_page_thumbnails(_tiny_pdf(), "pdf", "src-1") == 0


def test_maybe_store_gates(monkeypatch):
    monkeypatch.setattr(get_settings(), "page_thumbnail_enabled", True, raising=False)
    # non-PDF ext and missing source_id both skip
    assert pt.maybe_store_page_thumbnails(b"x", "txt", "src-1") == 0
    assert pt.maybe_store_page_thumbnails(_tiny_pdf(), "pdf", None) == 0


def test_store_and_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "thumbnail_base_dir", lambda: tmp_path)
    n = pt.store_page_thumbnails("src-9", _tiny_pdf(3), dpi=40)
    assert n == 3
    assert (tmp_path / "src-9" / "manifest.json").exists()
    thumbs = pt.load_page_thumbnails("src-9")
    assert [t.page for t in thumbs] == [1, 2, 3]
    filtered = pt.load_page_thumbnails("src-9", page_nums=[2])
    assert [t.page for t in filtered] == [2]
    assert pt.load_page_thumbnails("no-such-source") == []


def test_is_chart_question():
    assert pt.is_chart_question("图3中曲线的峰值温度是多少？")
    assert pt.is_chart_question("What does Figure 2 show?")
    assert pt.is_chart_question("谱图中有几个特征峰")
    assert not pt.is_chart_question("这个配方的pH应该控制在多少")
    assert not pt.is_chart_question("")


def test_estimate_image_tokens():
    # 40-DPI A4 (~331x467): formula upscales shortest side to 768px
    # -> 768x1083 -> 2x3 tiles -> 6*170+85 = 1105 tokens
    assert pt.estimate_image_tokens(331, 467) == 1105
    assert pt.estimate_image_tokens(0, 0) == 0
    # large square image downscales to 768x768 -> 2x2 tiles -> 4*170+85 = 765
    assert pt.estimate_image_tokens(2000, 2000) == 765


class _FakeCfg:
    provider = "deepseek"
    model = "deepseek-v4-flash-vision-exp"
    api_key = "k"
    base_url = None
    max_tokens = 100
    timeout = 10.0
    extra_headers = None


def test_answer_disabled_zero_overhead(monkeypatch):
    monkeypatch.setattr(get_settings(), "vlm_fallback_enabled", False, raising=False)

    def _boom(*a, **k):
        raise AssertionError("vision must not be called when disabled")

    monkeypatch.setattr("app.services.vision_extract._call_vision", _boom)
    assert pt.answer_chart_question("图1是什么？", "src-1") is None


def test_answer_chart_question(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "vlm_fallback_enabled", True, raising=False)
    monkeypatch.setattr(pt, "thumbnail_base_dir", lambda: tmp_path)
    pt.store_page_thumbnails("src-2", _tiny_pdf(2), dpi=40)

    monkeypatch.setattr(
        "app.services.vision_extract.vision_available", lambda: (True, "")
    )
    monkeypatch.setattr(
        "app.services.llm_roles.resolve_role", lambda role: _FakeCfg()
    )
    calls = []

    def _fake_call(cfg, prompt, content, filename):
        calls.append((prompt, filename))
        assert content[:8] == b"\x89PNG\r\n\x1a\n"
        return "峰值在 5.2"

    monkeypatch.setattr("app.services.vision_extract._call_vision", _fake_call)

    res = pt.answer_chart_question("图1曲线的峰值是多少？", "src-2")
    assert res is not None
    assert "5.2" in res.answer
    assert res.pages_used == [1, 2]
    thumbs = pt.load_page_thumbnails("src-2")
    expected = sum(pt.estimate_image_tokens(t.width, t.height) for t in thumbs)
    assert res.image_tokens == expected > 0
    assert res.model == "deepseek-v4-flash-vision-exp"
    assert len(calls) == 2


def test_answer_vlm_max_pages_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "vlm_fallback_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "vlm_max_pages", 1, raising=False)
    monkeypatch.setattr(pt, "thumbnail_base_dir", lambda: tmp_path)
    pt.store_page_thumbnails("src-3", _tiny_pdf(3), dpi=40)
    monkeypatch.setattr(
        "app.services.vision_extract.vision_available", lambda: (True, "")
    )
    monkeypatch.setattr(
        "app.services.llm_roles.resolve_role", lambda role: _FakeCfg()
    )
    calls = []
    monkeypatch.setattr(
        "app.services.vision_extract._call_vision",
        lambda cfg, prompt, content, filename: calls.append(filename) or "ok",
    )
    res = pt.answer_chart_question("图2呢？", "src-3")
    assert res is not None and res.pages_used == [1] and len(calls) == 1


def test_answer_vision_unavailable(monkeypatch):
    monkeypatch.setattr(get_settings(), "vlm_fallback_enabled", True, raising=False)
    monkeypatch.setattr(
        "app.services.vision_extract.vision_available",
        lambda: (False, "no key configured"),
    )
    res = pt.answer_chart_question("图1是什么？", "src-1")
    assert res is not None and res.hint == "no key configured" and res.answer == ""


def test_answer_non_chart_question_skipped(monkeypatch):
    # Not chart-like and no explicit pages -> None without touching vision.
    monkeypatch.setattr(get_settings(), "vlm_fallback_enabled", True, raising=False)

    def _boom(*a, **k):
        raise AssertionError("vision must not run for non-chart questions")

    monkeypatch.setattr("app.services.vision_extract._call_vision", _boom)
    assert pt.answer_chart_question("配方的pH控制在多少？", "src-1") is None
