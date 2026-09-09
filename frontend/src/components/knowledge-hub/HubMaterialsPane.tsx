import { useState } from "react";
import SourceDetailModal from "../SourceDetailModal";
import { resolveOpenUrl, type HubMaterialRow } from "./types";
import { useHubMaterialRows } from "./useHubMaterialRows";

const STATUS_LABEL: Record<string, string> = {
  queued: "待入库",
  fetching: "获取全文",
  indexing: "入库中",
  indexed: "已入库",
  skipped: "已在库",
  failed: "失败",
};

export default function HubMaterialsPane({ open }: { open: boolean }) {
  const {
    rows,
    loading,
    error,
    filter,
    setFilter,
    busyKey,
    msg,
    refreshKb,
    ingestRow,
    deleteRow,
    toggleSourceSelected,
  } = useHubMaterialRows(open);

  const [detail, setDetail] = useState<{ title: string; sourceId: string } | null>(null);
  const [snippetRow, setSnippetRow] = useState<HubMaterialRow | null>(null);

  function preview(row: HubMaterialRow) {
    if (row.source_id) {
      setDetail({ title: row.title, sourceId: row.source_id });
      return;
    }
    setSnippetRow(row);
  }

  function openSrc(row: HubMaterialRow) {
    const u = resolveOpenUrl(row);
    if (!u) return;
    window.open(u, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="flex flex-col gap-2 h-full min-h-0" data-testid="hub-materials-pane">
      <div className="flex flex-wrap items-center gap-2 shrink-0">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="筛选标题 / 公开号 / URL…"
          className="flex-1 min-w-[180px] bg-ink border border-edge rounded px-2 py-1.5 text-sm"
        />
        <button
          type="button"
          onClick={() => void refreshKb()}
          className="text-xs px-2 py-1.5 border border-edge rounded hover:border-accent/40"
        >
          刷新
        </button>
        <span className="text-[11px] text-slate-500">{rows.length} 条</span>
      </div>
      {msg && (
        <div className="text-[11px] text-teal-300/90 border border-teal-500/30 rounded px-2 py-1 shrink-0">
          {msg}
        </div>
      )}
      {error && (
        <div className="text-[11px] text-rose-300 border border-rose-500/40 rounded px-2 py-1 shrink-0">
          {error}
        </div>
      )}
      <div className="overflow-auto border border-edge/60 rounded flex-1 min-h-0">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-panel border-b border-edge text-slate-400">
            <tr>
              <th className="px-2 py-1.5 w-8">选</th>
              <th className="px-2 py-1.5">标题</th>
              <th className="px-2 py-1.5 w-24">来源</th>
              <th className="px-2 py-1.5">URL / 标识</th>
              <th className="px-2 py-1.5 w-16">状态</th>
              <th className="px-2 py-1.5 w-44">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge/40">
            {loading && rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-slate-500">
                  加载知识库文档…
                </td>
              </tr>
            )}
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-slate-500">
                  暂无资料。请先在左栏检索或上传，或等待后台自动入库。
                </td>
              </tr>
            )}
            {rows.map((row) => {
              const openUrl = resolveOpenUrl(row);
              const busy = busyKey === row.row_key;
              return (
                <tr key={row.row_key} className="hover:bg-accent/5" data-testid={`hub-row-${row.row_key}`}>
                  <td className="px-2 py-1.5 align-top">
                    {row.evidence ? (
                      <input
                        type="checkbox"
                        checked={!!row.selected}
                        onChange={() => toggleSourceSelected(row.identifier)}
                        title="用于问答勾选"
                      />
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 align-top text-slate-200 max-w-[220px]">
                    <div className="truncate font-medium" title={row.title}>
                      {row.title}
                    </div>
                    <div className="text-[10px] text-slate-500">
                      {row.kind === "kb" ? "知识库" : "会话"}
                    </div>
                  </td>
                  <td className="px-2 py-1.5 align-top text-slate-400">{row.source}</td>
                  <td className="px-2 py-1.5 align-top max-w-[240px]">
                    <div className="truncate text-slate-400 font-mono" title={openUrl || row.identifier}>
                      {openUrl || row.identifier}
                    </div>
                  </td>
                  <td className="px-2 py-1.5 align-top">
                    {row.kb_status ? (
                      <span className="text-[10px] border border-edge rounded px-1">
                        {STATUS_LABEL[row.kb_status] || row.kb_status}
                      </span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 align-top">
                    <div className="flex flex-wrap gap-1.5 text-[11px]">
                      <button
                        type="button"
                        className="text-accent hover:underline disabled:opacity-40"
                        onClick={() => preview(row)}
                      >
                        预览
                      </button>
                      <button
                        type="button"
                        className="text-accent hover:underline disabled:opacity-40"
                        disabled={!openUrl}
                        title={openUrl ? "打开源文件/网页" : "无可用 URL"}
                        onClick={() => openSrc(row)}
                      >
                        打开
                      </button>
                      <button
                        type="button"
                        className="text-teal-300 hover:underline disabled:opacity-40"
                        disabled={busy || row.kb_status === "fetching" || row.kb_status === "indexing"}
                        onClick={() => void ingestRow(row)}
                      >
                        {busy ? "…" : row.source_id ? "再入库" : "入库全文"}
                      </button>
                      <button
                        type="button"
                        className="text-rose-300/90 hover:underline disabled:opacity-40"
                        disabled={busy}
                        onClick={() => void deleteRow(row)}
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {detail && (
        <SourceDetailModal
          title={detail.title}
          sourceId={detail.sourceId}
          onClose={() => setDetail(null)}
        />
      )}
      {snippetRow && (
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
          onClick={() => setSnippetRow(null)}
        >
          <div
            className="bg-panel border border-edge rounded-xl max-w-lg w-full p-4 text-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between gap-2 mb-2">
              <h3 className="text-slate-100 font-medium truncate">{snippetRow.title}</h3>
              <button type="button" className="text-slate-400" onClick={() => setSnippetRow(null)}>
                ×
              </button>
            </div>
            <p className="text-slate-400 text-xs whitespace-pre-wrap max-h-64 overflow-auto">
              {snippetRow.snippet || "（无摘要；请先入库全文后预览切块）"}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
