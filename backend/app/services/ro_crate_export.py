"""Wave D2 — lightweight RO-Crate / steel-stamp portable package.

Inspired by AIPOCH ``ro-crate-export.ts`` lightweight profile: metadata +
SHA-256 references; bytes optional (complete profile deferred).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import zipfile
from typing import Any, Literal

logger = logging.getLogger(__name__)

RO_CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
LIGHTWEIGHT_PROFILE = "urn:formumind:ro-crate-profile:steel-stamp-lightweight"
Kind = Literal["storm", "dossier"]


def ro_crate_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "ro_crate_export_enabled", False))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes((text or "").encode("utf-8"))


def _load_report_markdown(project_id: str, kind: Kind) -> tuple[str, dict[str, Any]]:
    """Return (markdown, meta) for storm or dossier report."""
    from ..db.wiki_store import get_wiki_store
    from .wiki.schema import project_report_path

    store = get_wiki_store()
    templates = ("storm",) if kind == "storm" else ("briefing", "report", "dossier")
    row = None
    path = ""
    for tpl in templates:
        path = project_report_path(project_id, tpl)
        row = store.get_by_path(path)
        if row is not None:
            break
    if row is None:
        raise LookupError(f"{kind} report not found")
    md = store.read_markdown(row.path) or ""
    meta = {
        "path": row.path,
        "title": row.title or "",
        "source_ids": list(row.source_ids or []),
        "flags": list(row.flags or []),
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }
    return md, meta


def build_lightweight_ro_crate(
    project_id: str,
    *,
    kind: Kind = "storm",
    actor: str = "user",
    settings: Any = None,
    include_fulltext_bytes: bool = False,
) -> tuple[bytes, str]:
    """Build a lightweight RO-Crate zip.

    Returns (zip_bytes, filename).
    """
    from ..config import get_settings
    from . import literature_manifest as lm
    from .publication_preflight import get_state

    settings = settings or get_settings()
    if not ro_crate_enabled(settings):
        raise PermissionError("ro_crate_export_enabled is false")

    kind = kind if kind in ("storm", "dossier") else "storm"
    md, report_meta = _load_report_markdown(project_id, kind)
    md_bytes = md.encode("utf-8")
    md_hash = _sha256_bytes(md_bytes)

    man = lm.load_manifest(project_id) if lm.manifest_enabled(settings) else {}
    man_json = json.dumps(man, ensure_ascii=False, indent=2)
    man_bytes = man_json.encode("utf-8")
    man_hash = _sha256_bytes(man_bytes)

    try:
        preflight = get_state(project_id, kind)
    except Exception:  # noqa: BLE001
        preflight = {"project_id": project_id, "kind": kind, "findings": []}
    preflight_json = json.dumps(preflight, ensure_ascii=False, indent=2)
    preflight_bytes = preflight_json.encode("utf-8")
    preflight_hash = _sha256_bytes(preflight_bytes)

    steel = {
        "schema_version": 1,
        "project_id": project_id,
        "kind": kind,
        "exported_at": time.time(),
        "actor": actor,
        "profile": LIGHTWEIGHT_PROFILE,
        "report": {**report_meta, "sha256": md_hash, "bytes_included": True},
        "literature_manifest": {
            "sha256": man_hash,
            "frozen": man.get("frozen"),
            "coverage": man.get("coverage"),
            "bytes_included": True,
        },
        "preflight": {
            "sha256": preflight_hash,
            "open_blocking": preflight.get("open_blocking"),
            "open_major": preflight.get("open_major"),
            "ready": preflight.get("ready"),
            "bytes_included": True,
        },
        "honesty": {
            "traceability": "metadata+checksums packaged",
            "replay": "not claimed",
            "scientific_validity": "not claimed",
            "missing_evidence": "reported as unavailable when absent",
        },
    }
    steel_json = json.dumps(steel, ensure_ascii=False, indent=2)
    steel_bytes = steel_json.encode("utf-8")

    # Optional: attach frozen-item fulltext files if present on disk (D4b lite).
    fulltext_entries: list[tuple[str, bytes, str]] = []
    if include_fulltext_bytes:
        for item in man.get("items") or []:
            if not (man.get("frozen") or {}).get("item_ids"):
                break
            if str(item.get("id")) not in set((man.get("frozen") or {}).get("item_ids") or []):
                continue
            # Best-effort: no guaranteed PDF path; skip when unavailable.
            path_hint = item.get("fulltext_path") or item.get("pdf_path")
            if not path_hint:
                continue
            try:
                from pathlib import Path

                p = Path(str(path_hint))
                if p.is_file() and p.stat().st_size < 25_000_000:
                    raw = p.read_bytes()
                    name = f"data/fulltext/{p.name}"
                    fulltext_entries.append((name, raw, _sha256_bytes(raw)))
            except Exception as exc:  # noqa: BLE001
                logger.debug("ro-crate fulltext skip %s: %s", path_hint, exc)

    graph: list[dict[str, Any]] = [
        {
            "@type": "CreativeWork",
            "@id": "ro-crate-metadata.json",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            "about": {"@id": "./"},
        },
        {
            "@type": "Dataset",
            "@id": "./",
            "name": f"FormuMind steel-stamp package ({kind})",
            "description": (
                "Lightweight portable package: report markdown, literature manifest, "
                "and publication preflight state. Does not claim deterministic replay "
                "or scientific validity."
            ),
            "datePublished": time.strftime("%Y-%m-%d"),
            "hasPart": [
                {"@id": "report.md"},
                {"@id": "literature-manifest.json"},
                {"@id": "preflight.json"},
                {"@id": "steel-stamp.json"},
            ]
            + [{"@id": name} for name, _, _ in fulltext_entries],
            "additionalProperty": [
                {"@type": "PropertyValue", "name": "profile", "value": LIGHTWEIGHT_PROFILE},
                {"@type": "PropertyValue", "name": "project_id", "value": project_id},
                {"@type": "PropertyValue", "name": "kind", "value": kind},
            ],
        },
        {
            "@type": "File",
            "@id": "report.md",
            "name": "report.md",
            "encodingFormat": "text/markdown",
            "contentSize": str(len(md_bytes)),
            "sha256": md_hash,
        },
        {
            "@type": "File",
            "@id": "literature-manifest.json",
            "name": "literature-manifest.json",
            "encodingFormat": "application/json",
            "contentSize": str(len(man_bytes)),
            "sha256": man_hash,
        },
        {
            "@type": "File",
            "@id": "preflight.json",
            "name": "preflight.json",
            "encodingFormat": "application/json",
            "contentSize": str(len(preflight_bytes)),
            "sha256": preflight_hash,
        },
        {
            "@type": "File",
            "@id": "steel-stamp.json",
            "name": "steel-stamp.json",
            "encodingFormat": "application/json",
            "contentSize": str(len(steel_bytes)),
            "sha256": _sha256_bytes(steel_bytes),
            "description": "FormuMind steel-stamp honesty summary",
        },
    ]
    for name, raw, digest in fulltext_entries:
        graph.append(
            {
                "@type": "File",
                "@id": name,
                "name": name.split("/")[-1],
                "contentSize": str(len(raw)),
                "sha256": digest,
                "description": "Frozen corpus fulltext bytes (optional complete-lite)",
            }
        )

    metadata_doc = {"@context": RO_CRATE_CONTEXT, "@graph": graph}
    metadata_bytes = (json.dumps(metadata_doc, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ro-crate-metadata.json", metadata_bytes)
        zf.writestr("report.md", md_bytes)
        zf.writestr("literature-manifest.json", man_bytes)
        zf.writestr("preflight.json", preflight_bytes)
        zf.writestr("steel-stamp.json", steel_bytes)
        for name, raw, _ in fulltext_entries:
            zf.writestr(name, raw)

    filename = f"formumind-{kind}-{project_id[:40]}-ro-crate.zip"
    filename = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    return buf.getvalue(), filename
