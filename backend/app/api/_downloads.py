"""Headers for file downloads."""
from __future__ import annotations

import re
from urllib.parse import quote

_UNSAFE = re.compile(r'[\x00-\x1f\x7f"\\/;]')


def attachment_headers(filename: str, *, inline: bool = False) -> dict[str, str]:
    """``Content-Disposition`` for a user-influenced file name (RFC 6266 / 5987).

    A bare ``filename="..."`` breaks in two ways: Starlette encodes header values as latin-1, so a Chinese name (the usual
    case here: ``实验报告.pdf``) turns the download into a 500; and a ``"`` in the name ends the quoted string and lets the
    uploader append header parameters. The ASCII ``filename=`` is a fallback for old clients, ``filename*`` carries the
    real name.
    """
    name = _UNSAFE.sub("_", (filename or "").strip()) or "download"
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    disposition = "inline" if inline else "attachment"
    return {
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
    }


def ascii_header_value(value: str) -> str:
    """A header value that survives latin-1 encoding (non-ASCII characters percent-encoded, control characters dropped)."""
    return quote(_UNSAFE.sub("", value or ""), safe="/:._-~ ")
