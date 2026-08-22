"""The MinerU cloud adapter, driven against a faked SDK.

Two properties matter more than any individual behaviour here, because both
are ways this could quietly cost money or lose data:

* it never raises — a cloud parser that can take down an upload is worse than
  no cloud parser at all;
* it never sends what it does not have to — disabled, unconfigured, oversized
  and cached inputs must not reach the network.

The fake mirrors `mineru-open-sdk` 0.2.5: `extract(source: str, ...)` takes a
path (not bytes), returns an `ExtractResult` with `state`/`markdown`/
`content_list`/`images`, and signals failure with typed exceptions.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field

import pytest

from app.config import get_settings
from app.services import mineru_cloud


# ── a fake SDK ───────────────────────────────────────────────────────────────


@dataclass
class _FakeImage:
    name: str
    data: bytes
    path: str


@dataclass
class _FakeResult:
    state: str = "done"
    markdown: str | None = "# Parsed\n\nbody"
    content_list: list[dict] | None = None
    images: list[_FakeImage] = field(default_factory=list)
    error: str | None = None
    task_id: str = "t-1"


def _make_sdk(*, result=None, raise_name: str | None = None,
              batch_results: list[_FakeResult] | None = None) -> types.ModuleType:
    """A stand-in `mineru` module with the real exception taxonomy.

    Failures are named rather than passed in as instances: each call mints a
    fresh set of exception classes, so an instance built from one module would
    not be caught by `except mineru.AuthError` in another — the adapter would
    fall through to its generic handler and the test would pass for the wrong
    reason.
    """
    module = types.ModuleType("mineru")

    class MinerUError(Exception): ...
    class AuthError(MinerUError): ...
    class QuotaExceededError(MinerUError): ...
    class FileTooLargeError(MinerUError): ...
    class PageLimitError(MinerUError): ...
    class TimeoutError_(MinerUError): ...
    class TaskNotFoundError(MinerUError): ...

    module.MinerUError = MinerUError
    module.AuthError = AuthError
    module.QuotaExceededError = QuotaExceededError
    module.FileTooLargeError = FileTooLargeError
    module.PageLimitError = PageLimitError
    module.TimeoutError = TimeoutError_
    module.TaskNotFoundError = TaskNotFoundError
    module._Unexpected = ValueError          # not a MinerUError at all
    module.calls = []
    module.sources_existed = []
    module.batch_calls = []                  # list[list[str]] — 每次 extract_batch 的源
    module.batch_sources_existed = []        # list[list[bool]]

    class _Client:
        def __init__(self, token=None, base_url=None):
            module.calls.append({"token": token, "base_url": base_url})

        def extract(self, source, **kwargs):
            import os

            # The SDK takes a path; assert the file is actually there when it
            # is handed over, since the caller deletes it in a finally block.
            module.sources_existed.append(os.path.isfile(source))
            module.last_kwargs = kwargs
            module.last_source = source
            if raise_name is not None:
                raise getattr(module, raise_name)("simulated failure")
            return result if result is not None else _FakeResult()

        def extract_batch(self, sources, **kwargs):
            import os

            module.batch_calls.append(list(sources))
            module.batch_sources_existed.append(
                [os.path.isfile(s) for s in sources]
            )
            module.last_batch_kwargs = kwargs
            if raise_name is not None:
                raise getattr(module, raise_name)("simulated failure")
            if batch_results is not None:
                return iter(batch_results)
            return iter([
                _FakeResult(
                    content_list=[{"type": "text", "page_idx": 0, "text": "BATCH TEXT"}]
                )
                for _ in sources
            ])

        def get_task(self, task_id):
            if raise_name is not None:
                raise getattr(module, raise_name)("simulated failure")
            return _FakeResult()

    module.MinerU = _Client
    return module


@pytest.fixture()
def sdk(monkeypatch: pytest.MonkeyPatch):
    """Install the fake SDK and enable the feature with a token."""
    module = _make_sdk()
    monkeypatch.setitem(sys.modules, "mineru", module)
    monkeypatch.setattr(mineru_cloud, "optional_import", lambda name: name == "mineru")
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "test-token", raising=False)
    # 缓存测试需要缓存开启（生产默认 prune_mineru_cache=True 会跳过缓存）。
    monkeypatch.setattr(settings, "prune_mineru_cache", False, raising=False)
    return module


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        get_settings(), "mineru_cache_dir", str(tmp_path / "cache"), raising=False
    )


def _install(monkeypatch: pytest.MonkeyPatch, module) -> None:
    monkeypatch.setitem(sys.modules, "mineru", module)
    monkeypatch.setattr(mineru_cloud, "optional_import", lambda name: name == "mineru")


# ── gating: nothing reaches the network unless it should ─────────────────────


def test_disabled_by_default() -> None:
    """Pages are uploaded to a third party, so this is opt-in."""
    assert get_settings().mineru_enabled is False


def test_flag_off_means_no_call(sdk, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "mineru_enabled", False, raising=False)
    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is None
    assert sdk.calls == []


def test_missing_token_means_no_call(sdk, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "mineru_api_key", None, raising=False)
    ok, hint = mineru_cloud.mineru_available()
    assert ok is False and "Token" in hint
    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is None
    assert sdk.calls == []


def test_missing_sdk_means_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_cloud, "optional_import", lambda name: False)
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)
    ok, hint = mineru_cloud.mineru_available()
    assert ok is False and "mineru-open-sdk" in hint


def test_unsupported_format_is_refused_locally(sdk) -> None:
    assert mineru_cloud.parse_bytes(b"x", ext="txt") is None
    assert sdk.calls == []


def test_oversized_input_is_refused_before_the_round_trip(
    sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file the server will reject should not cost a round trip, and on a
    metered API it should not risk costing quota."""
    monkeypatch.setattr(get_settings(), "mineru_max_upload_mb", 1, raising=False)
    assert mineru_cloud.parse_bytes(b"x" * (2 * 1024 * 1024), ext="pdf") is None
    assert sdk.calls == []


def test_office_and_image_formats_are_accepted() -> None:
    """MinerU takes Office documents and images too — that is what lets a
    legacy .doc and a rendered scan page use the same adapter."""
    for ext in ("pdf", "doc", "docx", "pptx", "xlsx", "png", "jpg"):
        assert ext in mineru_cloud.SUPPORTED_EXTS


# ── the happy path ───────────────────────────────────────────────────────────


def test_blocks_are_normalised(sdk, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _FakeResult(
        markdown="# Doc",
        content_list=[
            {"type": "text", "page_idx": 0, "text": "body"},
            {"type": "title", "page_idx": 0, "text": "Heading", "text_level": 1},
            {"type": "equation", "page_idx": 1, "text": r"E = mc^2", "text_format": "latex"},
            {"type": "table", "page_idx": 1, "table_body": "<table></table>",
             "table_caption": ["Table 1"]},
            {"type": "image", "page_idx": 2, "img_path": "images/a.png",
             "image_caption": ["Figure 1"]},
        ],
        images=[_FakeImage(name="a.png", data=b"PNGDATA", path="images/a.png")],
    )
    _install(monkeypatch, _make_sdk(result=result))

    doc = mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf")
    assert doc is not None
    assert [b.type for b in doc.blocks] == ["text", "title", "equation", "table", "image"]
    assert doc.blocks[1].text_level == 1
    assert doc.blocks[2].text == "E = mc^2"          # LaTeX arrives as text
    assert doc.blocks[3].html == "<table></table>"   # table arrives as HTML
    assert doc.blocks[3].caption == "Table 1"        # list captions are joined
    assert doc.blocks[4].image == b"PNGDATA"         # image bytes are attached


def test_the_temp_file_exists_when_the_sdk_reads_it(sdk) -> None:
    """The SDK takes a path, not bytes, and the caller deletes the file in a
    finally block — so the ordering has to be right."""
    mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf")
    assert sdk.sources_existed == [True]


def test_the_temp_file_is_deleted_afterwards(sdk) -> None:
    """Uploaded content is proprietary formulation data; it must not be left
    lying on disk."""
    import os

    mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf")
    assert not os.path.exists(sdk.last_source)


def test_the_temp_file_is_deleted_even_when_the_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    module_with_error = _make_sdk(raise_name="MinerUError")
    _install(monkeypatch, module_with_error)
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)

    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is None
    assert not os.path.exists(module_with_error.last_source)


def test_ocr_flag_is_forwarded(sdk) -> None:
    mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf", ocr=True)
    assert sdk.last_kwargs["ocr"] is True


def test_unfinished_task_is_not_treated_as_a_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _make_sdk(result=_FakeResult(state="failed", error="nope")))
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)
    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is None


# ── failure taxonomy: every one degrades, none raises ────────────────────────


@pytest.mark.parametrize(
    "error_name",
    ["AuthError", "QuotaExceededError", "FileTooLargeError",
     "PageLimitError", "TimeoutError", "MinerUError"],
)
def test_every_sdk_failure_degrades_to_none(
    error_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, _make_sdk(raise_name=error_name))
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)

    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is None


def test_an_unexpected_exception_also_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _make_sdk(raise_name="_Unexpected"))
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)
    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is None


# ── cache: quota is per day, so this is money ────────────────────────────────


def test_second_call_is_served_from_cache(sdk) -> None:
    first = mineru_cloud.parse_bytes(b"%PDF-1.4 unique", ext="pdf")
    second = mineru_cloud.parse_bytes(b"%PDF-1.4 unique", ext="pdf")
    assert first is not None and second is not None
    assert second.cached is True
    assert len(sdk.calls) == 1, "the same bytes must not be parsed twice"


def test_prune_cache_default_skips_cache(sdk, monkeypatch: pytest.MonkeyPatch) -> None:
    """生产默认 prune_mineru_cache=True：不读缓存，每次都真正解析。"""
    monkeypatch.setattr(get_settings(), "prune_mineru_cache", True, raising=False)
    first = mineru_cloud.parse_bytes(b"%PDF-1.4 prune", ext="pdf")
    second = mineru_cloud.parse_bytes(b"%PDF-1.4 prune", ext="pdf")
    assert first is not None and second is not None
    assert second.cached is False
    assert len(sdk.calls) == 2, "prune 时每次都真正解析（不读缓存）"


def test_prune_cache_writes_nothing(sdk, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """prune 时不写缓存目录（不累积磁盘）。"""
    monkeypatch.setattr(get_settings(), "prune_mineru_cache", True, raising=False)
    mineru_cloud.parse_bytes(b"%PDF-1.4 prune2", ext="pdf")
    cache_root = tmp_path / "cache"
    assert not cache_root.exists() or not any(cache_root.iterdir())


def test_cached_images_survive_the_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _FakeResult(
        content_list=[{"type": "image", "page_idx": 0, "img_path": "images/a.png"}],
        images=[_FakeImage(name="a.png", data=b"PNGDATA", path="images/a.png")],
    )
    module = _make_sdk(result=result)
    _install(monkeypatch, module)
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)
    monkeypatch.setattr(settings, "prune_mineru_cache", False, raising=False)

    mineru_cloud.parse_bytes(b"%PDF img", ext="pdf")
    cached = mineru_cloud.parse_bytes(b"%PDF img", ext="pdf")
    assert cached.cached is True
    assert cached.blocks[0].image == b"PNGDATA"
    assert len(module.calls) == 1


def test_failures_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caching a failure turns one network blip into a permanently broken
    document — the same rule chemtools._cached follows."""
    _install(monkeypatch, _make_sdk(raise_name="MinerUError"))
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)
    assert mineru_cloud.parse_bytes(b"%PDF flaky", ext="pdf") is None

    working = _make_sdk()
    _install(monkeypatch, working)
    assert mineru_cloud.parse_bytes(b"%PDF flaky", ext="pdf") is not None
    assert len(working.calls) == 1, "the retry must actually reach the SDK"


def test_ocr_and_non_ocr_are_cached_separately(sdk) -> None:
    """The same page parsed with OCR on is a different result; serving one for
    the other would be silently wrong."""
    mineru_cloud.parse_bytes(b"%PDF same", ext="pdf", ocr=False)
    mineru_cloud.parse_bytes(b"%PDF same", ext="pdf", ocr=True)
    assert len(sdk.calls) == 2


def test_unwritable_cache_does_not_break_parsing(
    sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mineru_cache_dir", "/proc/nope", raising=False)
    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is not None


# ── token probe (the Settings test button) ───────────────────────────────────


def test_probe_reports_a_valid_token(sdk) -> None:
    ok, message = mineru_cloud.probe_token()
    assert ok is True and message


def test_probe_reports_a_rejected_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _make_sdk(raise_name="AuthError"))
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_api_key", "bad", raising=False)
    ok, message = mineru_cloud.probe_token()
    assert ok is False
    assert "无效" in message or "过期" in message


def test_task_not_found_means_the_token_worked(monkeypatch: pytest.MonkeyPatch) -> None:
    """MinerU has no validation endpoint, so the probe asks for a task that
    cannot exist. 'No such task' is the success signal."""
    _install(monkeypatch, _make_sdk(raise_name="TaskNotFoundError"))
    monkeypatch.setattr(get_settings(), "mineru_api_key", "good", raising=False)
    ok, _ = mineru_cloud.probe_token()
    assert ok is True


def test_probe_without_a_token_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "mineru_api_key", None, raising=False)
    ok, message = mineru_cloud.probe_token()
    assert ok is False and "Token" in message


# ── registry wiring ──────────────────────────────────────────────────────────


def test_the_key_appears_in_the_settings_ui() -> None:
    from app.services.secrets_store import list_secret_status

    entry = next(s for s in list_secret_status() if s["id"] == "mineru_api_key")
    assert entry["env_key"] == "FORMUMIND_MINERU_API_KEY"
    assert entry["group"] == "parse"


def test_probe_secret_routes_to_the_mineru_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import secrets_store

    monkeypatch.setattr(mineru_cloud, "probe_token", lambda: (True, "ok"))
    assert secrets_store.probe_secret("mineru_api_key") == {"ok": True, "message": "ok"}


def test_the_feature_flag_is_registered() -> None:
    from app.services.env_flags import FLAG_REGISTRY

    flag = next(f for f in FLAG_REGISTRY if f.attr == "mineru_enabled")
    assert flag.env_key == "FORMUMIND_MINERU_ENABLED"
    assert "mineru.net" in flag.hint      # the third-party upload is disclosed


def test_the_sdk_is_installable_from_the_dependencies_ui() -> None:
    from app.services.dependencies import CATALOG

    entry = next(d for d in CATALOG if d.pip_name == "mineru-open-sdk")
    assert entry.import_name == "mineru"


# ── the SDK does not type every auth failure ─────────────────────────────────


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _http_error(status: int) -> Exception:
    """What httpx raises, in the shape the adapter inspects."""
    exc = Exception(f"Client error '{status}' for url ...")
    exc.response = _Response(status)
    return exc


@pytest.mark.parametrize("status,expected", [(401, True), (403, True), (500, False), (404, False)])
def test_auth_failures_are_recognised_by_status(status: int, expected: bool) -> None:
    """The SDK defines AuthError for A0202/A0211, but a rejected token never
    reaches it — the request 401s first and httpx.HTTPStatusError comes out
    raw. Verified against the live API on both extract and get_task."""
    assert mineru_cloud._is_auth_failure(_http_error(status)) is expected


def test_a_non_http_exception_is_not_an_auth_failure() -> None:
    assert mineru_cloud._is_auth_failure(ValueError("something else")) is False


def test_a_rejected_token_is_reported_as_a_token_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporting a 401 as '连接失败' sends the operator to check the network
    instead of the key — the wrong half of the system."""
    module = _make_sdk()

    class _Client:
        def __init__(self, **kw): ...
        def get_task(self, task_id):
            raise _http_error(401)

    module.MinerU = _Client
    _install(monkeypatch, module)
    monkeypatch.setattr(get_settings(), "mineru_api_key", "bad", raising=False)

    ok, message = mineru_cloud.probe_token()
    assert ok is False
    assert "Token" in message and "401" in message
    assert "连接失败" not in message


def test_a_rejected_token_during_a_parse_still_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _make_sdk()

    class _Client:
        def __init__(self, **kw): ...
        def extract(self, source, **kw):
            raise _http_error(401)

    module.MinerU = _Client
    _install(monkeypatch, module)
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "bad", raising=False)

    assert mineru_cloud.parse_bytes(b"%PDF-1.4", ext="pdf") is None


# ── timeout budget: a page and a document are not the same upload ────────────


def test_a_document_uses_the_document_timeout(
    sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mineru_timeout_s", 300.0, raising=False)
    mineru_cloud.parse_bytes(b"%PDF whole doc", ext="pdf")
    assert sdk.last_kwargs["timeout"] == 300


def test_an_explicit_timeout_overrides_the_document_default(
    sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller escalating twenty pages sequentially needs its own budget.

    Without this the per-page loop inherits the whole-document allowance, and
    an unreachable service is charged that allowance once per page.
    """
    monkeypatch.setattr(get_settings(), "mineru_timeout_s", 300.0, raising=False)
    mineru_cloud.parse_bytes(b"%PDF one page", ext="pdf", timeout=90.0)
    assert sdk.last_kwargs["timeout"] == 90


def test_a_timeout_is_reported_with_the_budget_that_was_actually_used(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """The log must name the number that expired, not the default it ignored."""
    import logging

    _install(monkeypatch, _make_sdk(raise_name="TimeoutError"))
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "test-token", raising=False)
    monkeypatch.setattr(settings, "mineru_timeout_s", 300.0, raising=False)

    with caplog.at_level(logging.WARNING):
        assert mineru_cloud.parse_bytes(b"%PDF slow", ext="pdf", timeout=90.0) is None

    assert "90" in caplog.text and "300" not in caplog.text


# ── batch submission: N pages in one round trip ──────────────────────────────


def _batch_result(state: str = "done", text: str = "BATCH TEXT", error=None) -> _FakeResult:
    if state == "done":
        return _FakeResult(
            content_list=[{"type": "text", "page_idx": 0, "text": text}],
        )
    return _FakeResult(state="failed", error=error, markdown=None, content_list=None)


def test_parse_pages_batch_submits_one_call(sdk) -> None:
    """Three pages must be one batch, not three round trips."""
    results = mineru_cloud.parse_pages_batch([b"%PDF 1", b"%PDF 2", b"%PDF 3"])
    assert len(sdk.batch_calls) == 1
    assert len(sdk.batch_calls[0]) == 3
    assert all(r is not None for r in results)
    assert [r.blocks[0].text for r in results] == ["BATCH TEXT"] * 3


def test_parse_pages_batch_maps_failures_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed page inside a batch is that page's None, not the whole batch."""
    module = _make_sdk(batch_results=[
        _batch_result("done", text="ONE"),
        _batch_result("failed", error="boom"),
        _batch_result("done", text="THREE"),
    ])
    _install(monkeypatch, module)
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)

    results = mineru_cloud.parse_pages_batch([b"a", b"b", b"c"])

    assert results[0] is not None and results[0].blocks[0].text == "ONE"
    assert results[1] is None
    assert results[2] is not None and results[2].blocks[0].text == "THREE"


def test_parse_pages_batch_deletes_temp_files(sdk) -> None:
    """Uploaded pages are proprietary data — temp files must not survive."""
    import os

    mineru_cloud.parse_pages_batch([b"%PDF a", b"%PDF b"])
    assert sdk.batch_sources_existed[0] == [True, True]
    for source in sdk.batch_calls[0]:
        assert not os.path.exists(source)


def test_parse_pages_batch_degrade_when_sdk_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single batch failure degrades every page to None, never raises."""
    _install(monkeypatch, _make_sdk(raise_name="MinerUError"))
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "t", raising=False)

    assert mineru_cloud.parse_pages_batch([b"%PDF a", b"%PDF b"]) == [None, None]


def test_parse_pages_batch_serves_cache_hits(sdk) -> None:
    """Same bytes served from cache on the second batch — one network call total."""
    first = mineru_cloud.parse_pages_batch([b"%PDF A", b"%PDF B"])
    second = mineru_cloud.parse_pages_batch([b"%PDF A", b"%PDF B"])
    assert first[0] is not None and second[0] is not None
    assert second[0].cached is True and second[1].cached is True
    assert len(sdk.batch_calls) == 1, "the second batch must not reach the network"


def test_parse_pages_batch_uses_the_batch_timeout(
    sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mineru_batch_timeout_s", 1800.0, raising=False)
    mineru_cloud.parse_pages_batch([b"%PDF one"])
    assert sdk.last_batch_kwargs["timeout"] == 1800
