"""Wiki path helpers and front-matter schema (W1)."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_KEY = re.compile(r"[^a-zA-Z0-9._\u4e00-\u9fff-]+")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_key(raw: str, *, limit: int = 120) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[\s\-–—_®™()]+", "-", s)
    s = _SAFE_KEY.sub("-", s).strip(".-")
    s = re.sub(r"-{2,}", "-", s)
    if not s:
        digest = hashlib.sha1((raw or "unknown").encode("utf-8")).hexdigest()[:12]
        return f"u-{digest}"
    return s[:limit]


def material_path(norm_key: str) -> str:
    return f"materials/{safe_key(norm_key)}.md"


def system_path(param_name: str) -> str:
    return f"systems/{safe_key(param_name)}.md"


def mechanism_path(key: str) -> str:
    return f"mechanisms/{safe_key(key)}.md"


def pitfall_path(key: str) -> str:
    return f"pitfalls/{safe_key(key)}.md"


def chemical_path(*, cas: str | None = None, smiles: str | None = None, name: str | None = None) -> str:
    if cas:
        # Keep CAS hyphens (safe on disk); only strip path-hostile chars.
        key = re.sub(r"[^0-9A-Za-z._-]+", "", (cas or "").strip())[:40] or "unknown"
        return f"chemicals/{key}.md"
    if smiles:
        digest = hashlib.sha1(smiles.encode("utf-8")).hexdigest()[:16]
        return f"chemicals/smiles-{digest}.md"
    return f"chemicals/{safe_key(name or 'unknown')}.md"


def material_entity_id(norm_key: str) -> str:
    return f"material:{safe_key(norm_key)}"[:64]


def chemical_entity_id(*, cas: str | None = None, smiles: str | None = None) -> str:
    if cas:
        return f"chem:cas:{cas.strip()}"[:64]
    if smiles:
        digest = hashlib.sha1(smiles.encode("utf-8")).hexdigest()[:16]
        return f"chem:smiles:{digest}"[:64]
    return "chem:unknown"


def wiki_root_from_db_url(db_url: str) -> Path:
    """``sqlite:///./data/formumind.db`` → ``./data/wiki``."""
    raw = (db_url or "").replace("sqlite:///", "").strip()
    if not raw or raw == ":memory:":
        return Path("data") / "wiki"
    parent = Path(raw).expanduser().resolve().parent
    return parent / "wiki"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def dump_page(
    *,
    kind: str,
    title: str,
    entity_id: str,
    norm_key: str,
    source_ids: list[str],
    flags: list[str] | None = None,
    summary: str = "",
    evidence_blocks: list[str] | None = None,
    bounds: list[dict] | None = None,
    forbidden: list[str] | None = None,
) -> str:
    """Serialize a wiki markdown page with YAML-ish front matter (no PyYAML dep)."""
    import json

    flags = flags or []
    evidence_blocks = evidence_blocks or []
    bounds = bounds or []
    forbidden = forbidden or []
    src_list = ", ".join(f'"{s}"' for s in source_ids)
    flag_list = ", ".join(f'"{f}"' for f in flags)
    lines = [
        "---",
        f"kind: {kind}",
        f"title: {_yaml_escape(title)}",
        f"entity_id: {entity_id}",
        f"norm_key: {norm_key}",
        f"source_ids: [{src_list}]",
        f"flags: [{flag_list}]",
        f"updated_at: {utcnow_iso()}",
    ]
    if bounds:
        lines.append(f"bounds_json: {json.dumps(bounds, ensure_ascii=False)}")
    if forbidden:
        lines.append(f"forbidden_json: {json.dumps(forbidden, ensure_ascii=False)}")
    lines.extend(
        [
            "---",
            "",
            f"# {title}",
            "",
            "## Summary",
            summary.strip() or "_No summary yet._",
            "",
            "## Evidence",
        ]
    )
    if evidence_blocks:
        lines.extend(evidence_blocks)
    else:
        lines.append("_No evidence segments yet._")
    lines.append("")
    return "\n".join(lines)


def _yaml_escape(s: str) -> str:
    t = (s or "").replace("\n", " ").strip()
    if any(c in t for c in ":#{}[],&*?|>!%@`'\"\\"):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return t or '""'


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal front-matter parser for our dump format."""
    raw = text or ""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    header = raw[3:end].strip()
    body = raw[end + 4 :].lstrip("\n")
    meta: dict[str, Any] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip()
        # W4: keep JSON payloads as raw strings (bounds_json / forbidden_json).
        if key.endswith("_json"):
            meta[key] = val
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                meta[key] = []
            else:
                parts = []
                for p in inner.split(","):
                    p = p.strip().strip('"').strip("'")
                    if p:
                        parts.append(p)
                meta[key] = parts
        else:
            meta[key] = val.strip('"').strip("'")
    return meta, body


def extract_evidence_blocks(body: str) -> list[str]:
    """Return ``### Source ...`` blocks under ## Evidence."""
    if "## Evidence" not in body:
        return []
    after = body.split("## Evidence", 1)[1]
    # Stop at next H2 if any
    if "\n## " in after:
        after = after.split("\n## ", 1)[0]
    blocks: list[str] = []
    current: list[str] = []
    for ln in after.splitlines():
        if ln.startswith("### Source"):
            if current:
                blocks.append("\n".join(current).strip())
            current = [ln]
        elif current:
            current.append(ln)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def has_source_block(blocks: list[str], source_id: str) -> bool:
    needle = f"### Source `{source_id}`"
    return any(needle in b or b.startswith(f"### Source {source_id}") for b in blocks)
