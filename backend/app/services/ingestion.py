"""Local file ingestion service.

Converts uploaded files to Evidence objects via the unified parsing layer
(``services.parsing``: marker/MinerU/MarkItDown/pypdf cascade for PDFs,
MarkItDown + format fallbacks for everything else) and structure-aware
chunking (``services.chunking``: heading paths preserved, tables atomic).

Pipeline: parse → LLM source_guide → structure-aware chunk → persist SourceDocument.
"""
from __future__ import annotations

import logging
import hashlib
import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ..config import get_settings
from ..db.source_store import get_source_store
from ..domain.schemas import Evidence, SourceGuideSchema
from .errors import log_handled_exception
from .source_guide import extract_source_guide

logger = logging.getLogger(__name__)


@dataclass
class IngestOutcome:
    evidence: list[Evidence]
    source_id: str | None = None
    source_guide: SourceGuideSchema | None = None
    extraction_status: str = "skipped"
    # P2: 解析截断等用户可见提示（页数上限、OCR 降级…）。
    warnings: list[str] = field(default_factory=list)


def _parse_text(content: bytes) -> str:
    from .parsing import _parse_plain

    return _parse_plain(content) or ""


def _chunk_text(
    text: str, *, max_chars: int = 1600, overlap: int = 200, max_depth: int = 10
) -> list[str]:
    """Backward-compatible alias for the plain-text splitter (see chunking.py)."""
    from .chunking import chunk_plain_text

    return chunk_plain_text(text, max_chars=max_chars, overlap=overlap, max_depth=max_depth)


def _to_evidence(text: str, filename: str, *, source: str = "local") -> list[Evidence]:
    """Split text into chunk-level Evidence objects (structure-aware).

    Markdown heading paths are appended to chunk titles (``report (p.3) ·
    实施例 > 实施例 2``) so TF-IDF / ColBERT retrieval and citations see the
    document location; tables stay atomic.
    """
    from .chunking import chunk_markdown

    settings = get_settings()
    stem = Path(filename).stem if "." in filename else filename[:80]
    chunks = chunk_markdown(
        text,
        max_chars=settings.ingest_chunk_max_chars,
        overlap=settings.ingest_chunk_overlap,
    )
    chunks = [c for c in chunks if len(c.text.strip()) > 30]
    if not chunks and text.strip():
        from .chunking import Chunk

        chunks = [Chunk(text.strip()[:2000])]
    if not chunks:
        return []

    max_chunks = settings.ingest_max_chunks
    evidence: list[Evidence] = []
    for i, chunk in enumerate(chunks[:max_chunks]):
        ident = f"{stem}#{i}" if source == "local" else f"{filename}#{i}"
        title = stem if i == 0 else f"{stem} (p.{i+1})"
        if chunk.heading_path:
            title = f"{title} · {chunk.heading_path}"
        evidence.append(
            Evidence(
                source=source,
                identifier=ident,
                title=title,
                snippet=chunk.text[:500],
                relevance=1.0 - i * 0.01,
            )
        )
    return evidence


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _ingest_parsed_text(
    text: str,
    *,
    filename: str,
    source_kind: str,
    persist: bool = True,
    origin_url: str | None = None,
    parser: str | None = None,  # P3-5: ParseResult.parser provenance
) -> IngestOutcome:
    settings = get_settings()
    guide: SourceGuideSchema | None = None
    err: str | None = None
    status = "skipped"

    if settings.source_guide_enabled and text.strip() and settings.get_active_api_key():
        guide, err = extract_source_guide(text, title=filename)
        if guide and guide.status == "verified":
            status = "ok"
        elif guide:
            status = "degraded"
        else:
            status = "failed"
    elif settings.source_guide_enabled and text.strip() and not settings.get_active_api_key():
        status = "skipped"

    evidence = _to_evidence(text, filename, source=source_kind)

    source_id: str | None = None
    if persist and text.strip():
        store = get_source_store()
        source_id = store.create(
            filename=filename,
            title=Path(filename).stem if "." in filename else filename[:80],
            source_kind=source_kind,
            full_text=text,
            content_hash=_content_hash(text),
            source_guide=guide,
            extraction_status=status,
            extraction_error=err,
            origin_url=origin_url,
            parser=parser,
        )
        # Persistent KB v2: chunk (+embed when available) into document_chunks
        # so chat retrieval spans the whole corpus across restarts.
        from .kb_index import index_source

        try:
            # fail_soft=False: hard index errors must not leave an orphan
            # SourceDocument with zero chunks (create already committed).
            # v7 KB-1: 返回 0 也不抛错 —— 必须显式检查，否则零-chunk 孤儿行
            # 状态仍为 ok（与 _persist_fulltext 的 P1-8 口径一致）。
            from .kb_index import kb_enabled

            n_chunks = index_source(source_id, text, fail_soft=False)
            if not n_chunks and kb_enabled():
                try:
                    store.update_fields(
                        source_id,
                        ingest_status="failed",
                        ingest_error="index_source produced 0 chunks",
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("mark zero-chunk source failed (fail-open)")
                status = "failed"
                err = err or "kb index produced 0 chunks"
        except Exception as exc:
            log_handled_exception(logger, exc, "index_source hard fail — deleting orphan source")
            try:
                store.delete(source_id)
            except Exception as del_exc:
                log_handled_exception(logger, del_exc, "orphan source delete failed")
            source_id = None
            status = "failed"
            err = err or f"kb index failed: {type(exc).__name__}"
            return IngestOutcome(evidence, source_id, guide, status)
        _register_guide_products(source_id, guide)

    return IngestOutcome(evidence, source_id, guide, status)


def _register_guide_products(source_id: str | None, guide: SourceGuideSchema | None) -> None:
    """LLM-extracted commercial products → corpus product registry."""
    if guide is None or not getattr(guide, "products", None):
        return
    if not get_settings().product_extract_enabled:
        return
    try:
        from ..db.product_store import get_product_store

        get_product_store().upsert_mentions(
            source_id, [p.model_dump() for p in guide.products]
        )
    except Exception as exc:
        log_handled_exception(logger, exc, "guide product registration failed")


def _maybe_persist_mineru_structured(
    persist: bool, source_id: str | None, parsed
) -> None:
    """P2: MinerU 结构化产物统一落盘（extraction_tables/formulas）。

    上传与 URL 两条入库路径共用 —— 此前只接了上传路径。
    Fail-open: 失败只记日志，不破坏已成功的入库。
    """
    if not persist or not source_id or getattr(parsed, "structured", None) is None:
        return
    try:
        from .mineru_structured import persist_structured

        persist_structured(source_id, parsed.structured)
    except Exception:  # noqa: BLE001
        logger.exception("structured persist failed (fail-open)")


def ingest_file(
    filename: str,
    content: bytes,
    *,
    persist: bool = True,
    origin_url: str | None = None,
) -> IngestOutcome:
    """Parse an uploaded file and return ingest outcome.

    ``origin_url`` is the provenance/dedup key stored on the row. Uploads pass
    ``upload:sha256:<digest of the bytes>`` so a re-upload of the same file is
    recognised *before* a second OCR pass — the content hash on the row is
    computed over the extracted text, which is only known after parsing.
    """
    from .parsing import parse_document
    from .parse_notices import collect as _collect_notices

    ext = Path(filename).suffix.lower().lstrip(".")

    if ext in _IMAGE_EXTS:
        return _ingest_image(filename, content, persist=persist, origin_url=origin_url)

    with _collect_notices() as _parse_warnings:
        parsed = parse_document(content, ext)
    text = parsed.markdown

    if not text or not text.strip():
        # Two very different failures used to look identical here. A missing
        # parser is a deployment problem the operator can fix in a minute; an
        # empty extraction from a working parser means the document itself
        # carries no text layer. Reporting both as a bland placeholder is why
        # an install with no parsers at all still answered 200.
        from .parsing import ParserUnavailable, can_parse, install_hint

        if not can_parse(ext):
            raise ParserUnavailable(ext, install_hint(ext))
        placeholder = Evidence(
            source="local",
            identifier=filename,
            title=filename,
            snippet=f"未能提取到文本（格式：{ext}）——可能是扫描件或纯图片文档。",
            relevance=0.5,
        )
        return IngestOutcome(
            evidence=[placeholder],
            extraction_status="skipped",
            warnings=list(_parse_warnings),
        )

    outcome = _ingest_parsed_text(
        text,
        filename=filename,
        source_kind="local",
        persist=persist,
        origin_url=origin_url,
        parser=getattr(parsed, "parser", None),
    )
    # Phase 1: MinerU structured products → extraction_tables/formulas.
    # Fail-open: a structured-persist failure must never break the ingest
    # that already succeeded.
    _maybe_persist_mineru_structured(persist, outcome.source_id, parsed)
    # W2-3/P2: table sidecar persisted here (not inside parse_document) so the
    # key is the real SourceDocument UUID — the same key load_tables(doc.id)
    # reads. Fail-open: a sidecar failure must never break the ingest that
    # succeeded.
    if persist and outcome.source_id:
        from .parsing import maybe_persist_table_sidecar

        maybe_persist_table_sidecar(outcome.source_id, parsed)
    # Phase 3: opt-in page thumbnails (PDF only). Fail-open inside the hook;
    # zero overhead when page_thumbnail_enabled is False (default).
    if persist and outcome.source_id:
        from .page_thumbnails import maybe_store_page_thumbnails

        maybe_store_page_thumbnails(content, ext, outcome.source_id)
    # P2: 解析截断提示带回给用户。
    outcome.warnings.extend(_parse_warnings)
    return outcome


_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"})

# v10: 未知二进制魔数表（提到模块级；P2-7 补 RIFF/WebP、gzip、7z、RAR）。
_BIN_MAGIC = (
    b"\x89PNG",          # PNG
    b"\xff\xd8",          # JPEG
    b"GIF8",              # GIF
    b"PK\x03\x04",        # ZIP / OOXML
    b"%PDF",              # PDF
    b"BM",                # BMP（2 字节 —— 必须用 startswith，见下）
    b"RIFF",              # WebP / AVI / WAV（RIFF 容器，WebP 在 [8:12] 为 b"WEBP"）
    b"\x1f\x8b",          # gzip
    b"7z\xbc\xaf\x27\x1c",  # 7z
    b"Rar!\x1a\x07",      # RAR
)


def _looks_like_binary(body: bytes) -> bool:
    """v10: 二进制启发式 —— 魔数前缀匹配 + NUL 检查。

    P2-5: 旧代码 ``body[:4] in _BIN_MAGIC`` 中 2 字节的 b"BM" 永不可能
    命中（4 字节切片不可能等于 2 字节），是死代码；此处改用 startswith。
    P2-6: NUL 检查前先排除 UTF-16/UTF-32 合法文本（NUL 是其正常字节，
    旧代码会误杀）；能解码且可打印字符占主导则视为文本。
    """
    for magic in _BIN_MAGIC:
        if body.startswith(magic):
            return True
    if b"\x00" not in body[:1024]:
        return False
    sample = body[:4096]
    for enc in ("utf-16", "utf-32"):
        try:
            text = sample.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        if text and sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t") / len(text) > 0.7:
            return False
    return True


def _ingest_image(
    filename: str,
    content: bytes,
    *,
    persist: bool = True,
    origin_url: str | None = None,
) -> IngestOutcome:
    """Image upload → VLM structured extraction → standard ingest pipeline."""
    from .vision_extract import extract_image, image_markdown

    settings = get_settings()
    extraction, err = extract_image(content, filename)
    if extraction is not None and (extraction.markdown.strip() or extraction.molecules):
        text = image_markdown(extraction, filename)
        outcome = _ingest_parsed_text(
            text,
            filename=filename,
            source_kind="image",
            persist=persist,
            origin_url=origin_url,
        )
        if (
            persist
            and outcome.source_id
            and settings.kg_enabled
            and settings.kg_multimodal_fusion_enabled
        ):
            from ..pipeline.multimodal_fusion import run_multimodal_kg_fusion_bytes

            fusion = run_multimodal_kg_fusion_bytes(
                content,
                filename,
                context=text[:4000],
                source_id=outcome.source_id,
                persist=True,
            )
            if fusion.warnings and fusion.vision_error:
                logger.info(
                    "multimodal fusion for %s: %s",
                    filename,
                    fusion.vision_error or fusion.warnings[0],
                )
        return outcome
    placeholder = Evidence(
        source="local",
        identifier=filename,
        title=filename,
        snippet=f"图片未能结构化解析：{err or '视觉模型无输出'}",
        relevance=0.4,
    )
    return IngestOutcome(evidence=[placeholder], extraction_status="skipped")


def _normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _host_resolves_to_blocked(host: str) -> bool:
    import socket

    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if _is_blocked_ip(addr):
                return True
    return False


def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    host = _normalize_host(host)
    if host in ("localhost", "localhost.localdomain", "0.0.0.0"):
        return False
    if host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        if _is_blocked_ip(ipaddress.ip_address(host)):
            return False
    except ValueError:
        pass
    if _host_resolves_to_blocked(host):
        return False
    return True


def _html_to_text(html: str) -> str:
    """HTML → Markdown/text via the unified parsing layer (trafilatura first)."""
    from .parsing import html_to_markdown

    return html_to_markdown(html)


def ingest_url(url: str, *, persist: bool = True) -> IngestOutcome:
    """Fetch a web page and convert to Evidence chunks."""
    if not _is_safe_url(url):
        raise ValueError("URL must be a public http(s) address")

    # Content-filter blocklist: skip marketplace / junk hosts before download.
    from .kb_retrieval_gate import is_blocked_origin_url

    if get_settings().content_filter_enabled and is_blocked_origin_url(url):
        return IngestOutcome(
            evidence=[
                Evidence(
                    source="web",
                    identifier=url,
                    title=url,
                    snippet="该域名在内容质量黑名单中，已跳过入库",
                    relevance=0.0,
                )
            ],
            extraction_status="skipped",
        )

    import httpx

    # follow_redirects=False + manual loop: every redirect target is re-checked
    # with _is_safe_url so an SSRF cannot pivot to an internal host via a 3xx.
    current_url = url
    headers = {"User-Agent": "FormuMind/0.1 (research platform)"}
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        # 风险3 修正：全程 stream=True。client.get() 会先把整个响应
        # eager-buffer 进内存，iter_bytes 的上限截断形同虚设 —— 超大文件
        # 在截断前就已占满内存。重定向 hop 只读 headers 就关连接。
        resp = None
        try:
            for _hop in range(4):  # initial + up to 3 redirects
                if resp is not None:
                    resp.close()
                req = client.build_request("GET", current_url, headers=headers)
                resp = client.send(req, stream=True)
                if not resp.is_redirect:
                    break
                location = resp.headers.get("location")
                if not location:
                    break
                current_url = str(httpx.URL(current_url).join(location))
                if not _is_safe_url(current_url):
                    raise ValueError(f"Redirect target not allowed: {current_url}")
            assert resp is not None
            # v7 解析-5: 4 跳耗尽仍是重定向 → 明确报错，不解析 3xx 空体。
            if resp.is_redirect:
                raise ValueError(f"Too many redirects for {url}")
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            # P2: URL 下载上限 —— 真正的流式读取，超限即停。
            max_bytes = int(get_settings().ingest_max_url_bytes or 0)
            chunks: list[bytes] = []
            total = 0
            truncated = False
            for chunk in resp.iter_bytes(65536):
                if max_bytes and total + len(chunk) > max_bytes:
                    truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)
            body = b"".join(chunks)
        finally:
            if resp is not None:
                resp.close()
        if truncated:
            logger.warning(
                "ingest_url: %s exceeds ingest_max_url_bytes (%d), skipped",
                current_url, max_bytes,
            )
            return IngestOutcome(
                evidence=[
                    Evidence(
                        source="web",
                        identifier=url,
                        title=url,
                        snippet=f"下载超限（>{max_bytes // 1024 // 1024} MiB），已跳过入库",
                        relevance=0.0,
                    )
                ],
                extraction_status="skipped",
            )

    # P1-3: PDF 必须走 parse_document —— 裸字节 latin-1 解码会把二进制
    # 乱码写入 KB 污染检索。content-type 或魔数任一命中即判 PDF。
    is_pdf = "pdf" in content_type or body.lstrip()[:4] == b"%PDF"
    parsed = None
    _url_parse_warnings: list = []
    text = ""
    if "html" in content_type or body.lstrip()[:15].lower().startswith(b"<!doctype") or b"<html" in body[:500].lower():
        text = _html_to_text(body.decode("utf-8", errors="replace"))
    elif is_pdf:
        # v7 解析-4: 无 PDF 解析器时给出可操作的 hint（与 ingest_file 同口径）。
        from .parsing import can_parse, parse_document
        from .parse_notices import collect as _collect_notices

        if not can_parse("pdf"):
            from .parsing import ParserUnavailable, install_hint

            raise ParserUnavailable("pdf", install_hint("pdf"))
        with _collect_notices() as _url_parse_warnings:
            parsed = parse_document(body, "pdf")
        text = parsed.markdown or ""
    else:
        # v7: 非 PDF 二进制（docx/xlsx/pptx 等）不能 latin-1 裸解码。
        # 按 URL 扩展名或 content-type 映射到 parse_document；无法识别的
        # 二进制拒绝入库而非写乱码。
        from urllib.parse import urlparse

        from .parsing import can_parse, parse_document
        from .parse_notices import collect as _collect_notices

        _ct_to_ext = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
            "application/msword": "doc",
            "application/vnd.ms-excel": "xls",
            "application/vnd.ms-powerpoint": "ppt",
            "application/octet-stream": None,  # 按 URL 扩展名判断
        }
        # v8: 先算 url_ext（图片检查也要用）。
        url_ext = urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower() if "." in urlparse(url).path.rsplit("/", 1)[-1] else ""
        # v8: 先处理图片 URL —— v7 分流漏了所有非 zip 二进制，
        # PNG/JPG 会落入 else 被 latin-1 解码成乱码。
        # v9: 用模块级 _IMAGE_EXTS（含 tiff），不再局部遮蔽。
        if "image/" in content_type.lower() or url_ext in _IMAGE_EXTS:
            try:
                # v9: 透传 persist，否则预览/dry-run(persist=False) 也会写 DB。
                return _ingest_image(url, body, persist=persist, origin_url=url)
            except Exception as exc:
                # v9: IngestOutcome 字段是 warnings，不是 notices（v8 写错会 TypeError）。
                return IngestOutcome(
                    evidence=[],
                    extraction_status="skipped",
                    warnings=[f"图片 URL 解析失败：{exc}"],
                )
        ct_ext = _ct_to_ext.get(content_type.split(";")[0].strip().lower(), "")
        ext = url_ext if url_ext and can_parse(url_ext) else (ct_ext or "")
        # 二进制魔数启发：zip 包头（docx/xlsx/pptx 都是 zip）
        is_zip = body[:4] == b"PK\x03\x04"
        if ext and can_parse(ext):
            with _collect_notices() as _url_parse_warnings:
                parsed = parse_document(body, ext)
            text = parsed.markdown or ""
        elif ext in ("docx", "xlsx", "pptx", "doc", "xls", "ppt") or (url_ext in ("docx", "xlsx", "pptx", "doc", "xls", "ppt")):
            # v8: 格式可识别但无解析器 → 与 ingest_file 同口径抛 ParserUnavailable，
            # 而不是误导性的"不支持的二进制格式"。
            # v9: 补 hint 参数（ParserUnavailable(ext, hint)），否则 TypeError。
            from .parsing import ParserUnavailable, install_hint

            _fmt = url_ext or ext or "office"
            raise ParserUnavailable(_fmt, install_hint(_fmt))
        elif is_zip or (ct_ext is None and not url_ext):
            # 明确的二进制但无法识别格式 → 拒绝，不写乱码
            # v8: 先用文本启发式抢救（content-type 标错的纯文本）
            if b"\x00" not in body:
                try:
                    body.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    text = _parse_text(body)
            if text == "":
                return IngestOutcome(
                    evidence=[
                        Evidence(
                            source="web",
                            identifier=url,
                            title=url,
                            snippet=f"不支持的二进制格式（{content_type}），无法提取文本",
                            relevance=0.5,
                        )
                    ],
                    extraction_status="skipped",
                )
        else:
            # v9: 未知扩展名 + 二进制仍会 latin-1 乱码（P1-10）。
            # v10: 走模块级 _looks_like_binary（魔数 startswith + NUL 检查排除 UTF-16/32）。
            if _looks_like_binary(body):
                return IngestOutcome(
                    evidence=[
                        Evidence(
                            source="web",
                            identifier=url,
                            title=url,
                            snippet=f"不支持的二进制格式（{content_type}），无法提取文本",
                            relevance=0.5,
                        )
                    ],
                    extraction_status="skipped",
                )
            text = _parse_text(body)

    if not text.strip():
        return IngestOutcome(
            evidence=[
                Evidence(
                    source="web",
                    identifier=url,
                    title=url,
                    snippet="无法从该 URL 提取文本",
                    relevance=0.5,
                )
            ],
            extraction_status="skipped",
        )

    outcome = _ingest_parsed_text(
        text,
        filename=url,
        source_kind="web",
        persist=persist,
        origin_url=url,
    )
    if outcome.evidence:
        outcome.evidence[0].identifier = url
        outcome.evidence[0].title = url
    # Phase 3: opt-in page thumbnails for fetched PDFs (fail-open, zero
    # overhead when disabled).
    if persist and outcome.source_id and (
        "pdf" in content_type or body.lstrip()[:4] == b"%PDF"
    ):
        from .page_thumbnails import maybe_store_page_thumbnails

        maybe_store_page_thumbnails(body, "pdf", outcome.source_id)
    # P1-3/P2: URL 抓到的 PDF 表格同样落 sidecar（与 ingest_file 同键：UUID）。
    if persist and outcome.source_id:
        from .parsing import maybe_persist_table_sidecar

        maybe_persist_table_sidecar(outcome.source_id, parsed)
    # P2: MinerU 结构化产物 —— URL 路径此前漏接。
    _maybe_persist_mineru_structured(persist, outcome.source_id, parsed)
    # P2: 解析截断提示带回给用户。
    outcome.warnings.extend(_url_parse_warnings)
    return outcome


def ingest_text(text: str, title: str = "Pasted text", *, persist: bool = True) -> IngestOutcome:
    """Convert pasted plain text into Evidence chunks."""
    label = title.strip() or "Pasted text"
    if not text.strip():
        return IngestOutcome(evidence=[], extraction_status="skipped")

    outcome = _ingest_parsed_text(text, filename=label, source_kind="pasted", persist=persist)
    if outcome.evidence:
        outcome.evidence[0].title = label
        outcome.evidence[0].identifier = label
    elif text.strip():
        return IngestOutcome(
            evidence=[
                Evidence(
                    source="pasted",
                    identifier=label,
                    title=label,
                    snippet=text.strip()[:500],
                    relevance=1.0,
                )
            ],
            extraction_status=outcome.extraction_status,
            source_id=outcome.source_id,
            source_guide=outcome.source_guide,
        )
    return outcome


def _record_batch_failure(name: str, origin_url: str | None, error: str) -> None:
    """P2: 批量入库单文件失败可观测 —— 记入 record_ingest_failure。

    上传文件无 origin URL 时用 ``upload://文件名`` 合成 key，保证失败
    可查询、可复活。Fail-open：记录本身永不破坏批量流程。
    """
    try:
        from ..db.source_store import get_source_store

        get_source_store().record_ingest_failure(
            origin_url=origin_url or f"upload://{name}",
            filename=name,
            title=name,
            source_kind="local",
            project_id=None,
            error=error,
        )
    except Exception:  # noqa: BLE001
        logger.warning("record batch ingest failure failed (fail-open)", exc_info=True)


def ingest_files_batch(
    files: list[tuple[str, bytes]],
    *,
    persist: bool = True,
    origin_url_by_name: dict[str, str] | None = None,
) -> IngestOutcome:
    from .parsing import ParserUnavailable

    all_evidence: list[Evidence] = []
    all_warnings: list[str] = []
    last_outcome: IngestOutcome | None = None
    for name, content in files:
        origin = (origin_url_by_name or {}).get(name)
        try:
            outcome = ingest_file(
                name,
                content,
                persist=persist,
                origin_url=origin,
            )
        except ParserUnavailable as exc:
            # One unsupported file must not discard the other nineteen. Name
            # the file and the reason so it is obvious which one to fix.
            logger.warning("batch ingest: %s unparseable (%s)", name, exc.hint)
            _record_batch_failure(name, origin, f"ParserUnavailable: {exc.hint}")
            all_evidence.append(
                Evidence(
                    source="local",
                    identifier=name,
                    title=name,
                    snippet=f"未解析：{exc.hint}",
                    relevance=0.5,
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001
            # P2: 单文件一般异常不杀死整批 —— 记录失败行后继续下一个。
            logger.exception("batch ingest: %s failed", name)
            _record_batch_failure(name, origin, f"{type(exc).__name__}: {exc}")
            all_evidence.append(
                Evidence(
                    source="local",
                    identifier=name,
                    title=name,
                    snippet=f"入库失败：{type(exc).__name__}",
                    relevance=0.5,
                )
            )
            continue
        all_evidence.extend(outcome.evidence)
        for w in outcome.warnings:
            all_warnings.append(f"{name}：{w}")
        last_outcome = outcome
    return IngestOutcome(
        evidence=all_evidence,
        source_id=last_outcome.source_id if last_outcome else None,
        source_guide=last_outcome.source_guide if last_outcome else None,
        extraction_status=last_outcome.extraction_status if last_outcome else "skipped",
        warnings=all_warnings,
    )
