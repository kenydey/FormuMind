/**
 * 资料详情(B1/B2, 2026-09-05): 查看该文档在 KB 中的全部切块(页码/段落,
 * 回答引用可追溯) + 一键「链入知识图谱」提取实体建立关系。
 */
import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import { api, type KbChunk } from "../api";

export default function SourceDetailModal({
  title,
  sourceId,
  onClose,
}: {
  title: string;
  sourceId: string;
  onClose: () => void;
}) {
  const [chunks, setChunks] = useState<KbChunk[] | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);
  const [linkReport, setLinkReport] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.kbChunksBySource(sourceId);
      setChunks(Array.isArray(res) ? res : (res as { chunks?: KbChunk[] }).chunks ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [sourceId]);

  useEffect(() => {
    if (sourceId) void load();
  }, [sourceId, load]);

  async function linkToKg() {
    setLinking(true);
    setLinkReport(null);
    setError(null);
    try {
      const r = await api.kgLinkSource(sourceId);
      setLinkReport(`图谱已更新: ${r.entities_upserted} 实体 / ${r.relations_upserted} 关系 / ${r.links_created} 链接`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLinking(false);
    }
  }

  return (
    <Modal title={`📄 资料切块 · ${title.slice(0, 40)}`} open onClose={onClose} size="lg" testId="modal-source-detail">
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className="text-[10px] border border-edge rounded-full px-2 py-1 text-slate-400 hover:text-accent"
          >
            刷新
          </button>
          <button
            type="button"
            disabled={linking}
            onClick={() => void linkToKg()}
            className="text-[10px] border border-accent2/50 rounded-full px-2 py-1 text-accent2 hover:bg-accent2/10 disabled:opacity-50"
            title="从全文提取实体并建立到知识图谱(实体/关系由 NLP 链路产出)"
          >
            {linking ? "链入中…" : "🕸 链入知识图谱"}
          </button>
          {linkReport && <span className="text-[11px] text-emerald-400">{linkReport}</span>}
        </div>
        {error && <div className="text-xs text-rose-400 bg-rose-500/10 rounded p-2">{error}</div>}
        {busy ? (
          <div className="text-xs text-slate-500 py-8 text-center">切块加载中…</div>
        ) : chunks && chunks.length > 0 ? (
          <div className="space-y-2 max-h-[55vh] overflow-auto pr-1">
            {chunks.map((c, i) => {
              const text = (c.text ?? c.content ?? "").trim();
              if (!text) return null;
              const loc =
                c.page != null
                  ? `p.${c.page}${c.paragraph != null ? ` · ¶${c.paragraph}` : ""}`
                  : c.paragraph != null
                    ? `¶${c.paragraph}`
                    : c.offset != null
                      ? `+${c.offset}`
                      : "";
              return (
                <div key={c.chunk_id ?? i} className="border border-edge rounded p-2 bg-ink/30">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[9px] font-mono text-slate-600">#{i + 1}</span>
                    {loc && <span className="text-[9px] font-mono text-slate-500">{loc}</span>}
                  </div>
                  <p className="text-[11px] text-slate-300 whitespace-pre-wrap leading-relaxed">
                    {text.slice(0, 600)}
                    {text.length > 600 && <span className="text-slate-600"> …</span>}
                  </p>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-xs text-slate-500 py-8 text-center">
            该文档暂无切块(可能未入库或处于示例语料)。
          </div>
        )}
      </div>
    </Modal>
  );
}
