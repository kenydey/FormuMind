"""Wave E-Lit — BibTeX / RIS import-export for literature library (hand-rolled subset)."""
from __future__ import annotations

import re
import time
from typing import Any, Literal

from . import literature_import_ids as import_ids
from . import literature_manifest as lm

Format = Literal["bibtex", "ris"]
Scope = Literal["library", "frozen", "collection"]


def _cite_key(item: dict[str, Any]) -> str:
    doi = str(item.get("doi") or "").strip().lower()
    if doi:
        key = re.sub(r"[^a-z0-9]+", "_", doi)[:48]
        return key.strip("_") or "item"
    iid = str(item.get("id") or "item")
    return re.sub(r"[^a-zA-Z0-9]+", "_", iid)[:48].strip("_") or "item"


def _escape_bib(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
    )


def _select_items(
    man: dict[str, Any],
    *,
    scope: Scope,
    collection_id: str = "",
) -> list[dict[str, Any]]:
    items = [lm.ensure_item_library_fields(dict(i)) for i in (man.get("items") or [])]
    if scope == "library":
        return items
    if scope == "frozen":
        fids = set((man.get("frozen") or {}).get("item_ids") or [])
        return [i for i in items if str(i.get("id")) in fids]
    if scope == "collection":
        cid = (collection_id or "").strip()
        if not cid:
            raise ValueError("collection_id required for scope=collection")
        return [i for i in items if cid in (i.get("collection_ids") or [])]
    raise ValueError("invalid scope")


def export_bibtex(
    project_id: str,
    *,
    scope: Scope = "library",
    collection_id: str = "",
    settings: Any = None,
) -> str:
    if settings is not None and not lm.manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not lm.library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    man = lm.load_manifest(project_id)
    items = _select_items(man, scope=scope, collection_id=collection_id)
    blocks: list[str] = []
    for item in items:
        key = _cite_key(item)
        fields: list[str] = []
        title = str(item.get("title") or "").strip()
        if title:
            fields.append(f"  title = {{{_escape_bib(title)}}}")
        authors = " and ".join(str(a) for a in (item.get("authors") or []) if str(a).strip())
        if authors:
            fields.append(f"  author = {{{_escape_bib(authors)}}}")
        if item.get("year") is not None:
            fields.append(f"  year = {{{int(item['year'])}}}")
        if item.get("doi"):
            fields.append(f"  doi = {{{_escape_bib(str(item['doi']))}}}")
        if item.get("url"):
            fields.append(f"  url = {{{_escape_bib(str(item['url']))}}}")
        if item.get("chemrxiv_id"):
            fields.append(f"  eprint = {{{_escape_bib(str(item['chemrxiv_id']))}}}")
            fields.append("  eprinttype = {chemrxiv}")
        note = str(item.get("notes") or "").strip()
        if note:
            fields.append(f"  note = {{{_escape_bib(note[:500])}}}")
        body = ",\n".join(fields)
        blocks.append(f"@article{{{key},\n{body}\n}}\n")
    return "\n".join(blocks)


def export_ris(
    project_id: str,
    *,
    scope: Scope = "library",
    collection_id: str = "",
    settings: Any = None,
) -> str:
    if settings is not None and not lm.manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not lm.library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    man = lm.load_manifest(project_id)
    items = _select_items(man, scope=scope, collection_id=collection_id)
    blocks: list[str] = []
    for item in items:
        is_chem = bool(item.get("chemrxiv_id")) or (
            "chemrxiv" in str(item.get("doi") or "").lower()
        )
        lines = [f"TY  - {'PREPRINT' if is_chem else 'JOUR'}"]
        if item.get("title"):
            lines.append(f"TI  - {str(item['title']).replace(chr(10), ' ').strip()}")
        for a in item.get("authors") or []:
            lines.append(f"AU  - {str(a).strip()}")
        if item.get("year") is not None:
            lines.append(f"PY  - {int(item['year'])}")
        if item.get("doi"):
            lines.append(f"DO  - {item['doi']}")
        if item.get("url"):
            lines.append(f"UR  - {item['url']}")
        if item.get("chemrxiv_id"):
            lines.append(f"N1  - ChemRxiv: {item['chemrxiv_id']}")
        note = str(item.get("notes") or "").strip()
        if note:
            lines.append(f"N1  - {note[:500].replace(chr(10), ' ')}")
        lines.append("ER  - ")
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


_BIB_ENTRY_RE = re.compile(
    r"@(?P<type>article|inproceedings|misc|phdthesis|book)\s*\{\s*(?P<key>[^,]+)\s*,(?P<body>.*?)\n\}",
    re.IGNORECASE | re.DOTALL,
)
_BIB_FIELD_RE = re.compile(
    r"(?P<name>[a-zA-Z]+)\s*=\s*\{(?P<value>[^{}]*)\}",
    re.DOTALL,
)


def parse_bibtex(text: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for m in _BIB_ENTRY_RE.finditer(text or ""):
        fields = {
            f.group("name").lower(): f.group("value").strip()
            for f in _BIB_FIELD_RE.finditer(m.group("body"))
        }
        eprinttype = (fields.get("eprinttype") or "").lower()
        url = fields.get("url") or ""
        if eprinttype == "arxiv" or "arxiv.org" in url.lower():
            failures.append(
                {
                    "token": m.group("key").strip(),
                    "reason": "unsupported_scheme",
                    "detail": "arxiv",
                }
            )
            continue
        doi = (fields.get("doi") or "").replace("https://doi.org/", "").strip().lower() or None
        chemrxiv_id = None
        if eprinttype == "chemrxiv" and fields.get("eprint"):
            chemrxiv_id = fields["eprint"].strip().lower()
        authors = []
        if fields.get("author"):
            authors = [a.strip() for a in re.split(r"\s+and\s+", fields["author"]) if a.strip()]
        year = None
        if fields.get("year"):
            try:
                year = int(re.search(r"\d{4}", fields["year"]).group(0))  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                year = None
        title = fields.get("title") or doi or chemrxiv_id or m.group("key").strip()
        iid = f"doi:{doi}" if doi else (
            f"chemrxiv:{chemrxiv_id}" if chemrxiv_id else f"bib:{m.group('key').strip()[:40]}"
        )
        items.append(
            lm.ensure_item_library_fields(
                {
                    "id": iid,
                    "title": title[:240],
                    "doi": doi,
                    "chemrxiv_id": chemrxiv_id,
                    "authors": authors[:40],
                    "year": year,
                    "url": url[:500] or None,
                    "notes": (fields.get("note") or "")[:4000],
                    "source": "ChemRxiv" if chemrxiv_id or (doi and "chemrxiv" in doi) else "bibtex_import",
                    "snippet": "",
                    "evidence_class": "import_ids",
                    "screening": "unset",
                }
            )
        )
    return items, failures


def parse_ris(text: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    current: dict[str, Any] = {"authors": [], "notes": []}
    def flush() -> None:
        nonlocal current
        if not current.get("title") and not current.get("doi") and not current.get("chemrxiv_id"):
            current = {"authors": [], "notes": []}
            return
        url = str(current.get("url") or "")
        if "arxiv.org" in url.lower():
            failures.append(
                {
                    "token": str(current.get("title") or url)[:80],
                    "reason": "unsupported_scheme",
                    "detail": "arxiv",
                }
            )
            current = {"authors": [], "notes": []}
            return
        doi = current.get("doi")
        chemrxiv_id = current.get("chemrxiv_id")
        iid = f"doi:{doi}" if doi else (
            f"chemrxiv:{chemrxiv_id}" if chemrxiv_id else f"ris:{hash(str(current.get('title'))) & 0xFFFFFFFF:x}"
        )
        notes = "\n".join(current.get("notes") or [])[:4000]
        items.append(
            lm.ensure_item_library_fields(
                {
                    "id": iid,
                    "title": str(current.get("title") or doi or chemrxiv_id or "untitled")[:240],
                    "doi": doi,
                    "chemrxiv_id": chemrxiv_id,
                    "authors": list(current.get("authors") or [])[:40],
                    "year": current.get("year"),
                    "url": url[:500] or None,
                    "notes": notes,
                    "source": "ChemRxiv" if chemrxiv_id or (doi and "chemrxiv" in str(doi)) else "ris_import",
                    "snippet": "",
                    "evidence_class": "import_ids",
                    "screening": "unset",
                }
            )
        )
        current = {"authors": [], "notes": []}

    for raw_line in (text or "").splitlines():
        m = re.match(r"^([A-Z0-9]{2})\s+-\s?(.*)$", raw_line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            if current.get("title") or current.get("doi"):
                flush()
            current = {"authors": [], "notes": []}
        elif tag == "ER":
            flush()
        elif tag == "TI" or tag == "T1":
            current["title"] = val
        elif tag == "AU" or tag == "A1":
            current.setdefault("authors", []).append(val)
        elif tag == "PY" or tag == "Y1":
            ym = re.search(r"(20\d{2}|19\d{2})", val)
            if ym:
                current["year"] = int(ym.group(1))
        elif tag == "DO" or tag == "DI":
            current["doi"] = val.replace("https://doi.org/", "").strip().lower()
        elif tag == "UR":
            current["url"] = val
            if "arxiv.org" in val.lower():
                current["url"] = val  # flagged on flush
        elif tag == "N1" or tag == "AB":
            cm = re.match(r"(?i)chemrxiv:\s*(\S+)", val)
            if cm:
                current["chemrxiv_id"] = cm.group(1).lower()
            else:
                current.setdefault("notes", []).append(val)
    if current.get("title") or current.get("doi"):
        flush()
    return items, failures


def import_citation(
    project_id: str,
    *,
    format: Format,
    text: str,
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not lm.manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not lm.library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    if format == "bibtex":
        items, failures = parse_bibtex(text)
    elif format == "ris":
        items, failures = parse_ris(text)
    else:
        raise ValueError("format must be bibtex|ris")

    added: list[str] = []
    skipped: list[str] = []
    for item in items:
        # Prefer resolved metadata when DOI present
        if item.get("doi"):
            try:
                item = import_ids.resolve_doi_item(str(item["doi"]))
            except Exception:  # noqa: BLE001
                pass
        _, status = lm.upsert_library_item(
            project_id, item, settings=settings, clear_freeze_on_add=True
        )
        if status == "added":
            added.append(str(item["id"]))
        else:
            skipped.append(str(item["id"]))

    man = lm.load_manifest(project_id)
    man["events"] = (man.get("events") or [])[-180:] + [
        {
            "type": "citation_import",
            "at": time.time(),
            "format": format,
            "added": len(added),
            "skipped": len(skipped),
            "failed": len(failures),
        }
    ]
    man = lm.save_manifest(man)
    return {
        "project_id": project_id,
        "format": format,
        "added": added,
        "skipped": skipped,
        "failures": failures,
        "manifest": man,
    }
