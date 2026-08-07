"""MinerU cloud parsing — structured blocks for the pages worth paying for.

Used by the hybrid pipeline to escalate individual pages that local extraction
handles poorly: dense tables, formulas, and figures. Whole documents go through
only when there is no text layer to work with at all.

**Why the SDK and not raw REST.** The published REST flow is three calls and a
zip: request a presigned URL, PUT the file, poll a batch endpoint, download and
unpack `full_zip_url`. `mineru-open-sdk` does all of that, and — more valuable —
raises a *typed* exception per failure, so quota exhaustion, an expired token
and a page-limit rejection are distinguishable without matching error strings.
Reimplementing it would be a second code path to the same network dependency:
more surface, no more resilience. When the SDK is absent this module reports
unavailable and the caller keeps its local result, which is the actual fallback.

**Degradation.** Every public function returns `None`/empty rather than
raising. A cloud parser that can take down an upload would be worse than no
cloud parser at all.

⚠️ Verified against `mineru-open-sdk` 0.2.5: `MinerU.extract` accepts a **path
or URL, not bytes** (hence the temp file), and the SDK's `PageLimitError`
documents a **600**-page ceiling even though the web docs say 200.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from .errors import degrade_return, optional_import
from .runtime_secrets import effective_setting

logger = logging.getLogger(__name__)

# Formats MinerU accepts. Images are included deliberately: it is how a
# rendered scan page gets escalated.
SUPPORTED_EXTS = frozenset(
    {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
     "png", "jpg", "jpeg", "jp2", "webp", "gif", "bmp"}
)


@dataclass
class MinerUBlock:
    """One block of MinerU's `content_list`, normalised.

    The block *type* is the routing signal the fusion layer needs: `equation`
    already carries LaTeX and `table` already carries HTML, so neither has
    anything to gain from a vision model, while `image` and `chart` have
    nothing else to offer it.
    """

    type: str
    page_idx: int = 0
    text: str = ""
    text_level: int = 0
    html: str = ""
    caption: str = ""
    image_path: str = ""
    image: bytes | None = None


@dataclass
class MinerUDocument:
    blocks: list[MinerUBlock] = field(default_factory=list)
    markdown: str = ""       # MinerU's own full.md — the fallback if fusion fails
    cached: bool = False


def mineru_available() -> tuple[bool, str]:
    """(usable, actionable Chinese hint) — the shape `vision_available` uses."""
    settings = get_settings()
    if not settings.mineru_enabled:
        return False, "MinerU 云端解析未启用（FORMUMIND_MINERU_ENABLED）"
    if not effective_setting(settings, "mineru_api_key"):
        return False, "未配置 MinerU API Token，请在「设置 → API 配置」填写"
    if not optional_import("mineru"):
        return False, (
            "未安装 mineru-open-sdk。请在「设置 → 依赖管理」安装，"
            "或 pip install mineru-open-sdk。"
        )
    return True, ""


# ── content-hash cache ───────────────────────────────────────────────────────


def _is_auth_failure(exc: Exception) -> bool:
    """Whether *exc* is really "your token was rejected".

    The SDK defines `AuthError` for codes A0202/A0211, but a rejected token
    does not reach it: the request 401s first and `httpx.HTTPStatusError`
    comes out raw. Verified against the live API on both `extract` and
    `get_task`. Without this, a wrong token is reported as a connection
    failure and sends the operator to check the network instead of the key.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (401, 403)


def _cache_root() -> Path:
    return Path(get_settings().mineru_cache_dir).expanduser()


def _cache_key(content: bytes, *, ext: str, ocr: bool) -> str:
    """Hash the bytes *and* the options — the same page parsed with OCR on is
    a different result, and serving one for the other would be silently wrong."""
    digest = hashlib.sha256(content).hexdigest()
    return f"{digest}-{ext}-{'ocr' if ocr else 'txt'}"


def _cache_load(key: str) -> MinerUDocument | None:
    path = _cache_root() / key / "doc.json"
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text("utf-8"))
        blocks = []
        for raw in payload.get("blocks", []):
            image_path = raw.get("image_path", "")
            image = None
            if image_path:
                stored = path.parent / "images" / Path(image_path).name
                if stored.is_file():
                    image = stored.read_bytes()
            blocks.append(MinerUBlock(**{**raw, "image": image}))
        return MinerUDocument(
            blocks=blocks, markdown=payload.get("markdown", ""), cached=True
        )
    except Exception as exc:
        logger.debug("mineru cache read failed for %s (%s)", key, exc)
        return None


def _cache_store(key: str, document: MinerUDocument) -> None:
    """Persist to disk, not memory: quota is per day, and a restart in the
    middle of a batch should not mean paying for the same pages twice."""
    try:
        target = _cache_root() / key
        (target / "images").mkdir(parents=True, exist_ok=True)
        blocks = []
        for block in document.blocks:
            raw = {k: v for k, v in vars(block).items() if k != "image"}
            if block.image and block.image_path:
                name = Path(block.image_path).name
                (target / "images" / name).write_bytes(block.image)
            blocks.append(raw)
        (target / "doc.json").write_text(
            json.dumps({"blocks": blocks, "markdown": document.markdown},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        # A cache that cannot be written must never fail the parse it was
        # meant to speed up.
        logger.debug("mineru cache write failed for %s (%s)", key, exc)


# ── SDK call ─────────────────────────────────────────────────────────────────


def _normalise(result, images_by_path: dict[str, bytes]) -> MinerUDocument:
    blocks: list[MinerUBlock] = []
    for raw in result.content_list or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "")
        image_path = str(raw.get("img_path") or "")
        caption = raw.get("image_caption") or raw.get("table_caption") or ""
        if isinstance(caption, list):
            caption = " ".join(str(c) for c in caption)
        blocks.append(
            MinerUBlock(
                type=kind,
                page_idx=int(raw.get("page_idx") or 0),
                text=str(raw.get("text") or ""),
                text_level=int(raw.get("text_level") or 0),
                html=str(raw.get("table_body") or ""),
                caption=str(caption),
                image_path=image_path,
                image=images_by_path.get(image_path)
                or images_by_path.get(Path(image_path).name if image_path else ""),
            )
        )
    return MinerUDocument(blocks=blocks, markdown=result.markdown or "")


def parse_bytes(
    content: bytes, *, ext: str = "pdf", ocr: bool = False,
    timeout: float | None = None,
) -> MinerUDocument | None:
    """Parse *content* through MinerU, or return None and let the caller cope.

    None covers every reason a call cannot or should not happen: disabled, no
    token, no SDK, unsupported format, oversized, quota gone, network down.
    The caller keeps whatever it already had.

    *timeout* overrides `mineru_timeout_s` for this call. A single escalated
    page and a whole scanned document are not the same upload, and a caller that
    is going to make twenty sequential calls needs a much shorter one than a
    caller making a single big one.
    """
    ext = (ext or "").lower().lstrip(".")
    available, hint = mineru_available()
    if not available:
        logger.debug("mineru unavailable: %s", hint)
        return None
    if ext not in SUPPORTED_EXTS:
        logger.debug("mineru: .%s is not an accepted format", ext)
        return None

    settings = get_settings()
    limit = int(settings.mineru_max_upload_mb) * 1024 * 1024
    if len(content) > limit:
        # Refused locally on purpose: an upload that is going to be rejected
        # should not cost a round trip, and on a metered API it should not
        # risk costing quota either.
        logger.warning(
            "mineru: %.1f MB exceeds the %d MB limit — not sending",
            len(content) / 1024 / 1024, settings.mineru_max_upload_mb,
        )
        return None

    key = _cache_key(content, ext=ext, ocr=ocr)
    cached = _cache_load(key)
    if cached is not None:
        logger.info("mineru: cache hit (%s)", key[:12])
        return cached

    document = _extract(content, ext=ext, ocr=ocr, timeout=timeout)
    if document is not None:
        # Only successes are cached. Caching a failure would turn one network
        # blip into a permanently broken document.
        _cache_store(key, document)
    return document


def _extract(
    content: bytes, *, ext: str, ocr: bool, timeout: float | None = None
) -> MinerUDocument | None:
    import mineru  # type: ignore

    settings = get_settings()
    token = str(effective_setting(settings, "mineru_api_key") or "")
    wait = float(settings.mineru_timeout_s if timeout is None else timeout)

    handle, temp_path = tempfile.mkstemp(suffix=f".{ext}")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(content)

        client = mineru.MinerU(token=token, base_url=settings.mineru_base_url)
        result = client.extract(temp_path, ocr=ocr or None, timeout=int(wait))
        if getattr(result, "state", "") != "done":
            logger.warning(
                "mineru: task finished in state %r (%s)",
                getattr(result, "state", "?"), getattr(result, "error", ""),
            )
            return None

        images = {}
        for image in getattr(result, "images", None) or []:
            images[getattr(image, "path", "")] = image.data
            images[getattr(image, "name", "")] = image.data
        return _normalise(result, images)

    except mineru.AuthError as exc:
        # Actionable and distinct: the operator needs to replace a token, not
        # debug a parser.
        logger.error("mineru: token rejected (%s) — check 设置 → API 配置", exc)
        return None
    except mineru.QuotaExceededError as exc:
        logger.error("mineru: daily quota exhausted (%s) — falling back to local", exc)
        return None
    except (mineru.FileTooLargeError, mineru.PageLimitError) as exc:
        logger.warning("mineru: document rejected by size/page limit (%s)", exc)
        return None
    except mineru.TimeoutError as exc:
        logger.warning("mineru: timed out after %ss (%s)", wait, exc)
        return None
    except mineru.MinerUError as exc:
        return degrade_return(logger, exc, "mineru extract failed", None)
    except Exception as exc:
        if _is_auth_failure(exc):
            logger.error("mineru: token rejected (HTTP 401/403) — check 设置 → API 配置")
            return None
        return degrade_return(logger, exc, "mineru call failed", None)
    finally:
        # The uploaded file is proprietary formulation data. Delete it whatever
        # happened, including on the exception paths above.
        try:
            os.unlink(temp_path)
        except OSError:  # pragma: no cover - already gone
            pass


def probe_token() -> tuple[bool, str]:
    """Cheapest possible authentication check, for the Settings test button.

    MinerU publishes no token-validation or quota endpoint, so this asks for a
    task that cannot exist. A rejected token raises AuthError; anything else —
    including "no such task" — means the token was accepted.
    """
    settings = get_settings()
    token = str(effective_setting(settings, "mineru_api_key") or "")
    if not token:
        return False, "未配置 MinerU API Token"
    if not optional_import("mineru"):
        return False, "未安装 mineru-open-sdk（pip install mineru-open-sdk）"

    import mineru  # type: ignore

    try:
        client = mineru.MinerU(token=token, base_url=settings.mineru_base_url)
        client.get_task("00000000-0000-0000-0000-000000000000")
        return True, "Token 有效"
    except mineru.AuthError as exc:
        return False, f"Token 无效或已过期：{exc}"
    except mineru.TaskNotFoundError:
        # The expected answer: authenticated, and the task genuinely is absent.
        return True, "Token 有效"
    except mineru.MinerUError as exc:
        return True, f"Token 已接受（服务返回：{exc}）"
    except Exception as exc:
        if _is_auth_failure(exc):
            return False, "Token 无效或已过期（服务返回 401）"
        return False, f"连接失败：{exc}"
