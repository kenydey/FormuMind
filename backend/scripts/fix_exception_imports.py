#!/usr/bin/env python3
"""Add missing errors imports and loggers after exception refactor."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"

IMPORT_BY_PREFIX = {
    "services/deep_research/": "from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "services/engines/adapters/": "from ...errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "services/engines/": "from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "services/property/": "from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "services/": "from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "api/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "worker/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "db/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "pipeline/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "agents/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "domain/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
    "": "from .services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal",
}


def import_stmt(rel: str) -> str:
    for prefix, line in sorted(IMPORT_BY_PREFIX.items(), key=lambda x: -len(x[0])):
        if prefix and rel.startswith(prefix):
            return line
    return IMPORT_BY_PREFIX[""]


def needs_errors(text: str) -> bool:
    return any(
        tok in text
        for tok in ("degrade_return(", "log_handled_exception(", "optional_import(", "reraise_if_fatal(")
    )


def has_errors_import(text: str) -> bool:
    return "errors import degrade_return" in text or "errors import" in text and "log_handled_exception" in text


def has_logger(text: str) -> bool:
    return bool(re.search(r"^logger\s*=\s*logging\.getLogger", text, re.M)) or "logging.getLogger(__name__)" in text


def insert_after_header(text: str, line: str) -> str:
    if line in text:
        return text
    m = re.match(
        r'((?:\"\"\"[\s\S]*?\"\"\"\n|\'\'\'[\s\S]*?\'\'\'\n)?(?:from __future__ import[^\n]+\n\n)?)',
        text,
    )
    if m:
        return text[: m.end()] + line + "\n" + text[m.end() :]
    return line + "\n" + text


def ensure_logging(text: str) -> str:
    if has_logger(text):
        return text
    if "import logging" not in text:
        text = insert_after_header(text, "import logging")
    if not has_logger(text):
        # after imports block
        lines = text.splitlines(keepends=True)
        last_import = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                last_import = i + 1
        logger_line = "\nlogger = logging.getLogger(__name__)\n"
        lines.insert(last_import, logger_line)
        text = "".join(lines)
    return text


def main() -> None:
    for path in sorted(ROOT.rglob("*.py")):
        if path.name == "errors.py":
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        if not needs_errors(text):
            continue
        new = text
        if not has_errors_import(new):
            new = insert_after_header(new, import_stmt(rel))
        if "log_handled_exception(logger" in new or "degrade_return(logger" in new:
            new = ensure_logging(new)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print("patched", rel)


if __name__ == "__main__":
    main()
