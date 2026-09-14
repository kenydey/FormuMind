"""Project-scoped views for Knowledge Hub / Wiki browse.

Wiki pages have no ``project_id`` column; we attribute them via:
  - path conventions for dossier / reports (``themes/project-{id}.md``,
    ``reports/project-{id}-*.md``)
  - intersection of page ``source_ids`` with sources stamped for the project

Strict mode excludes global sources (``project_id IS NULL``).
"""
from __future__ import annotations

from typing import Any, Iterable

from ...db.source_store import get_source_store
from .schema import project_dossier_path, project_report_path, safe_key


def project_source_id_set(project_id: str, *, limit: int = 2000) -> set[str]:
    """SourceDocument ids owned by ``project_id`` (no globals)."""
    pid = (project_id or "").strip()
    if not pid:
        return set()
    rows = get_source_store().list_for_project(pid, limit=limit, include_global=False)
    return {r.id for r in rows if r.id}


def wiki_path_owned_by_project(path: str, project_id: str) -> bool:
    """True when path is this project's dossier or a project report."""
    pid = (project_id or "").strip()
    if not pid or not path:
        return False
    rel = path.replace("\\", "/")
    if rel == project_dossier_path(pid):
        return True
    sk = safe_key(pid)
    if rel.startswith(f"themes/project-{sk}."):
        return True
    if rel.startswith(f"reports/project-{sk}-"):
        return True
    # Any explicit report path helper match for known templates is covered by prefix.
    _ = project_report_path  # imported for callers / docs symmetry
    return False


def wiki_page_in_project(
    *,
    path: str,
    page_source_ids: Iterable[str] | None,
    project_id: str,
    allowed_source_ids: set[str] | None = None,
) -> bool:
    """Whether a wiki page should appear in a project-scoped Hub list."""
    pid = (project_id or "").strip()
    if not pid:
        return False
    if wiki_path_owned_by_project(path, pid):
        return True
    allowed = allowed_source_ids if allowed_source_ids is not None else project_source_id_set(pid)
    if not allowed:
        return False
    page_ids = {str(s) for s in (page_source_ids or []) if s}
    return bool(page_ids & allowed)


def filter_wiki_rows(rows: list[Any], project_id: str) -> list[Any]:
    """Filter ORM/wiki-like rows that expose ``.path`` and ``.source_ids``."""
    pid = (project_id or "").strip()
    if not pid:
        return []
    allowed = project_source_id_set(pid)
    out: list[Any] = []
    for row in rows:
        path = getattr(row, "path", None) or (row.get("path") if isinstance(row, dict) else "") or ""
        sids = getattr(row, "source_ids", None)
        if sids is None and isinstance(row, dict):
            sids = row.get("source_ids")
        if wiki_page_in_project(
            path=str(path),
            page_source_ids=sids,
            project_id=pid,
            allowed_source_ids=allowed,
        ):
            out.append(row)
    return out
