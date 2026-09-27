"""Artifact version diff service (W4-3 / P1-14).

Pure two-level diff for version content snapshots:

1. Line-level alignment with ``difflib.SequenceMatcher``.
2. Secondary character-level alignment for changed (replace) line blocks,
   so the frontend can highlight intra-line edits.

Degradation policy: when either text exceeds ``TRUNCATED_LIMIT_BYTES``
(200 KB), only line-level ops are produced and ``truncated=True`` is set —
character-level alignment is skipped to bound compute cost.

Execution policy: diffs are computed **synchronously**. The existing worker
machinery is Celery-based (``app/worker/tasks.py``: ``celery_app.task`` +
result registration / polling). Registering a new async task for diffs
would add task plumbing for a pure function that completes in well under a
second at typical sizes; even line-level-only diffs of >200 KB texts are
fast (SequenceMatcher with ``autojunk=False``). Synchronous computation +
truncated degradation is therefore used by design, documented here.

Op contract (consumed by the frontend version panel)::

    {"truncated": bool,
     "ops": [{"type": "equal"|"insert"|"delete",
              "old_text": str, "new_text": str}]}

``equal`` carries identical ``old_text``/``new_text``; ``insert`` has
``old_text == ""``; ``delete`` has ``new_text == ""``.
"""
from __future__ import annotations

import difflib
from collections import Counter
from typing import Any

#: Byte threshold above which only line-level diff is produced.
TRUNCATED_LIMIT_BYTES = 200 * 1024

#: A single changed line-block larger than this skips character-level
#: alignment even outside truncated mode (bounds the quadratic worst case
#: of SequenceMatcher on huge repetitive chunks).
_CHAR_ALIGN_LIMIT_BYTES = 64 * 1024


def _heavy_repetition(
    items: list[str] | str, *, min_items: int = 2000, min_top: int = 1000
) -> bool:
    """True when a single element dominates the input.

    This is the case that makes ``SequenceMatcher(autojunk=False)``
    quadratic (e.g. 50k identical lines): the popular element's index list
    is huge and every ``find_longest_match`` scan walks it. The check
    itself is O(n) via ``Counter``.
    """
    n = len(items)
    if n < min_items:
        return False
    top_count = Counter(items).most_common(1)[0][1]
    return top_count > max(min_top, n // 10)


def _op(op_type: str, old_text: str, new_text: str) -> dict[str, str]:
    return {"type": op_type, "old_text": old_text, "new_text": new_text}


def _char_ops(old_chunk: str, new_chunk: str) -> list[dict[str, str]]:
    """Secondary character-level alignment for a changed line block."""
    junky = _heavy_repetition(old_chunk, min_items=4096, min_top=2048) or _heavy_repetition(
        new_chunk, min_items=4096, min_top=2048
    )
    matcher = difflib.SequenceMatcher(None, old_chunk, new_chunk, autojunk=junky)
    ops: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.append(_op("equal", old_chunk[i1:i2], new_chunk[j1:j2]))
        elif tag == "delete":
            ops.append(_op("delete", old_chunk[i1:i2], ""))
        elif tag == "insert":
            ops.append(_op("insert", "", new_chunk[j1:j2]))
        else:  # replace → emit as delete + insert pair (contract has no "replace")
            ops.append(_op("delete", old_chunk[i1:i2], ""))
            ops.append(_op("insert", "", new_chunk[j1:j2]))
    return ops


def diff_versions(old_text: str, new_text: str) -> dict[str, Any]:
    """Diff two texts; returns ``{"truncated": bool, "ops": [...]}``.

    Line-level alignment first; changed line blocks get secondary
    character-level alignment unless either text exceeds
    ``TRUNCATED_LIMIT_BYTES`` (line-level only, ``truncated=True``).
    """
    old_bytes = len(old_text.encode("utf-8"))
    new_bytes = len(new_text.encode("utf-8"))
    truncated = old_bytes > TRUNCATED_LIMIT_BYTES or new_bytes > TRUNCATED_LIMIT_BYTES

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    # autojunk=False gives accurate diffs for repetitive-but-meaningful
    # content (e.g. formulation tables) on normal sizes. It stays off only
    # when safe: in truncated mode, or when a single line dominates the
    # input (the quadratic case — e.g. tens of thousands of identical
    # lines), autojunk is switched on so a request can never hang.
    # Degraded mode trades alignment accuracy for bounded cost; the ops
    # remain a correct diff (reassembly invariant holds).
    junky = (
        truncated
        or _heavy_repetition(old_lines)
        or _heavy_repetition(new_lines)
    )
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=junky)

    ops: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            block = "".join(old_lines[i1:i2])
            ops.append(_op("equal", block, block))
        elif tag == "delete":
            ops.append(_op("delete", "".join(old_lines[i1:i2]), ""))
        elif tag == "insert":
            ops.append(_op("insert", "", "".join(new_lines[j1:j2])))
        else:  # replace
            old_block = "".join(old_lines[i1:i2])
            new_block = "".join(new_lines[j1:j2])
            if truncated or (
                len(old_block.encode("utf-8")) > _CHAR_ALIGN_LIMIT_BYTES
                or len(new_block.encode("utf-8")) > _CHAR_ALIGN_LIMIT_BYTES
            ):
                ops.append(_op("delete", old_block, ""))
                ops.append(_op("insert", "", new_block))
            else:
                ops.extend(_char_ops(old_block, new_block))
    return {"truncated": truncated, "ops": ops}
