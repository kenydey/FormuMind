import { useCallback, useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, type Evidence, type KBSourceItem } from "../../api";
import { useStore } from "../../store";
import { idsMatch, patentIdAliases } from "../../utils/patentIds";
import type { HubMaterialRow } from "./types";

function sessionRow(e: Evidence, selected: boolean, kbHint?: { status?: string; source_id?: string | null }): HubMaterialRow {
  return {
    row_key: `ev:${e.identifier}`,
    kind: kbHint?.source_id ? "kb" : "session",
    title: e.title || e.identifier,
    source: e.source || "unknown",
    identifier: e.identifier,
    url: e.url,
    url_alt: e.url_alt,
    oa_pdf_url: e.oa_pdf_url,
    is_oa: e.is_oa,
    assignee: e.assignee,
    pub_date: e.pub_date,
    snippet: e.snippet,
    relevance: e.relevance,
    kb_status: kbHint?.status,
    source_id: kbHint?.source_id ?? null,
    selected,
    evidence: e,
  };
}

function kbOnlyRow(d: KBSourceItem): HubMaterialRow {
  return {
    row_key: `kb:${d.id}`,
    kind: "kb",
    title: d.title || d.filename || d.id,
    source: d.source_kind || "kb",
    identifier: d.origin_url || d.id,
    url: d.origin_url,
    kb_status: d.extraction_status || "indexed",
    source_id: d.id,
  };
}

/** Merge session Evidence + persisted KB sources into one Hub table. */
export function useHubMaterialRows(open: boolean) {
  const {
    sources,
    selectedSources,
    kbIngest,
    activeProjectId,
    removeSource,
    toggleSourceSelected,
  } = useStore(
    useShallow((s) => ({
      sources: s.sources,
      selectedSources: s.selectedSources,
      kbIngest: s.kbIngest,
      activeProjectId: s.activeProjectId,
      removeSource: s.removeSource,
      toggleSourceSelected: s.toggleSourceSelected,
    })),
  );

  const [kbDocs, setKbDocs] = useState<KBSourceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const refreshKb = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.kbSources(activeProjectId, 200);
      setKbDocs(list.sources ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setKbDocs([]);
    } finally {
      setLoading(false);
    }
  }, [activeProjectId]);

  useEffect(() => {
    if (open) void refreshKb();
  }, [open, refreshKb]);

  const rows = useMemo(() => {
    const selected = new Set(selectedSources);
    const ingestById = new Map<string, { status: string; source_id?: string | null }>();
    for (const d of kbIngest?.docs ?? []) {
      for (const a of patentIdAliases(d.identifier)) {
        ingestById.set(a, { status: d.status, source_id: d.source_id });
      }
      ingestById.set(d.identifier, { status: d.status, source_id: d.source_id });
    }

    const merged: HubMaterialRow[] = [];
    const coveredKb = new Set<string>();

    for (const e of sources) {
      let hint: { status?: string; source_id?: string | null } | undefined;
      for (const a of patentIdAliases(e.identifier)) {
        if (ingestById.has(a)) {
          hint = ingestById.get(a);
          break;
        }
      }
      // Match persisted KB by origin_url
      const kbHit = kbDocs.find(
        (d) =>
          idsMatch(d.origin_url, e.identifier) ||
          idsMatch(d.origin_url, e.url) ||
          idsMatch(d.id, hint?.source_id),
      );
      if (kbHit) {
        coveredKb.add(kbHit.id);
        hint = {
          status: hint?.status || kbHit.extraction_status || "indexed",
          source_id: kbHit.id,
        };
      }
      merged.push(sessionRow(e, selected.has(e.identifier), hint));
    }

    for (const d of kbDocs) {
      if (coveredKb.has(d.id)) continue;
      // Also skip if some session row already matched via alias but coveredKb missed
      const dup = sources.some(
        (e) => idsMatch(d.origin_url, e.identifier) || idsMatch(d.origin_url, e.url),
      );
      if (dup) continue;
      merged.push(kbOnlyRow(d));
    }

    // KB first, then session-only
    merged.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "kb" ? -1 : 1;
      return (a.title || "").localeCompare(b.title || "");
    });

    const q = filter.trim().toLowerCase();
    if (!q) return merged;
    return merged.filter(
      (r) =>
        r.title.toLowerCase().includes(q) ||
        r.identifier.toLowerCase().includes(q) ||
        r.source.toLowerCase().includes(q) ||
        (r.url || "").toLowerCase().includes(q),
    );
  }, [sources, selectedSources, kbDocs, kbIngest, filter]);

  async function ingestRow(row: HubMaterialRow) {
    const e = row.evidence;
    if (!e && !row.identifier) return;
    const key = row.row_key;
    setBusyKey(key);
    setMsg(null);
    try {
      const res = await api.ingestEvidence({
        identifier: row.identifier,
        title: row.title,
        url: row.url ?? undefined,
        url_alt: row.url_alt ?? undefined,
        source: row.source,
        project_id: activeProjectId,
        assignee: row.assignee ?? undefined,
        pub_date: row.pub_date ?? undefined,
        snippet: row.snippet,
        oa_pdf_url: row.oa_pdf_url ?? undefined,
        is_oa: row.is_oa ?? undefined,
        relevance: row.relevance ?? 0.9,
      });
      setMsg(
        res.ok
          ? res.status === "skipped"
            ? `已在库 · ${res.canonical_id || row.identifier}`
            : `全文已入库 · ${res.canonical_id || row.identifier}`
          : res.reason || "入库失败",
      );
      await refreshKb();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }

  async function deleteRow(row: HubMaterialRow) {
    const key = row.row_key;
    setBusyKey(key);
    setMsg(null);
    try {
      if (row.source_id) {
        const ok = window.confirm(
          `从知识库永久删除「${row.title}」？\n将移除切块与图谱提及，此操作不可撤销。`,
        );
        if (!ok) return;
        await api.deleteKbSource(row.source_id);
        setMsg(`已删除知识库文档 · ${row.source_id.slice(0, 8)}…`);
        await refreshKb();
        // Also drop from session if present
        if (row.evidence) removeSource(row.identifier);
      } else {
        const ok = window.confirm(`从当前会话列表移除「${row.title}」？`);
        if (!ok) return;
        removeSource(row.identifier);
        setMsg("已移出会话列表");
      }
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }

  return {
    rows,
    loading,
    error,
    filter,
    setFilter,
    busyKey,
    msg,
    setMsg,
    refreshKb,
    ingestRow,
    deleteRow,
    toggleSourceSelected,
  };
}
