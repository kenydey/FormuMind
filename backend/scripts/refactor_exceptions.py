#!/usr/bin/env python3
"""Codemod: tiered handling for broad ``except Exception`` handlers."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"

IMPORT_BY_PREFIX = {
    "services/": "from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "services/deep_research/": "from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "services/engines/": "from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "services/engines/adapters/": "from ...errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "services/property/": "from ..errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "": "from .services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "api/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "worker/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "db/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "pipeline/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "agents/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
    "domain/": "from ..services.errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal\n",
}


def import_line(rel: str) -> str:
    for prefix, line in sorted(IMPORT_BY_PREFIX.items(), key=lambda x: -len(x[0])):
        if prefix and rel.startswith(prefix):
            return line
    return IMPORT_BY_PREFIX[""]


def ensure_import(text: str, rel: str) -> str:
    line = import_line(rel)
    if "degrade_return" in text or "log_handled_exception" in text:
        return text
    # after module docstring / __future__
    m = re.match(r'((?:\"\"\"[\s\S]*?\"\"\"\n|\'\'\'[\s\S]*?\'\'\'\n)?(?:from __future__ import[^\n]+\n\n)?)', text)
    if m:
        insert_at = m.end()
        return text[:insert_at] + line + text[insert_at:]
    return line + text


def transform_source(text: str) -> str:
    original = text

    # logger.warning(..., exc) + return []/None/False/{} 
    text = re.sub(
        r"except Exception as exc:\n(\s+)logger\.warning\(([^)]+)\)\n\1return (\[\]|None|False|\{\}|\"\"|'')",
        lambda m: (
            f"except Exception as exc:\n{m.group(1)}return degrade_return(logger, exc, {m.group(2).split(',')[0].strip()}, {m.group(3)})"
        ),
        text,
    )

    # except Exception:\n    pass  -> log
    text = re.sub(
        r"except Exception:\n(\s+)pass\b",
        r"except Exception as exc:\n\1log_handled_exception(logger, exc, \"handled exception\")",
        text,
    )

    # except Exception:\n    return None (no log) after import try
    text = re.sub(
        r"(from [\w.]+ import [^\n]+\n\s+)except Exception:\n(\s+)return None\b",
        r"\1except ImportError:\n\2return None",
        text,
    )

    # except Exception:\n    return False in __import__ blocks
    text = re.sub(
        r"try:\n(\s+)__import__\(([^)]+)\)\n(\s+)return True\n(\s+)except Exception:\n(\s+)return False",
        r"return optional_import(\2)",
        text,
    )

    # except Exception:\n    return False (generic)
    text = re.sub(
        r"except Exception:\n(\s+)return False\b",
        r"except Exception as exc:\n\1log_handled_exception(logger, exc, \"optional feature check\")\n\1return False",
        text,
    )

    # except Exception:\n    return None
    text = re.sub(
        r"except Exception:\n(\s+)return None\b",
        r"except Exception as exc:\n\1return degrade_return(logger, exc, \"operation failed\", None)",
        text,
    )

    # except Exception as exc: raise LLM* - add reraise_if_fatal
    if "reraise_if_fatal" not in text:
        text = re.sub(
            r"(except Exception as exc:\n)(\s+)(if _is_auth_error)",
            r"\1\2reraise_if_fatal(exc)\n\2\3",
            text,
        )

    return text


def main() -> int:
    changed = 0
    for path in sorted(ROOT.rglob("*.py")):
        if path.name == "errors.py":
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel.startswith("services/errors"):
            continue
        src = path.read_text(encoding="utf-8")
        if "except Exception" not in src:
            continue
        new = transform_source(src)
        if "except Exception" in new and rel:
            new = ensure_import(new, rel + ("/" if not rel.endswith("/") else ""))
        if new != src:
            path.write_text(new, encoding="utf-8")
            changed += 1
            print("updated", path.relative_to(ROOT.parent))
    print(f"changed {changed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
