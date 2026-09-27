/**
 * W3-10: 配方证据谱系 —— 复用 LineageTree 的弹窗 + 纵向链视觉语言，
 * 数据源换成 provenance lineage API（claim→source / formulation→claim /
 * run→formulation 边，BFS 上游）。
 */
import { useEffect, useState } from "react";
import { api, formatApiError, type ProvenanceEdge } from "../api";

const TYPE_META: Record<string, { label: string; icon: string; cls: string }> = {
  formulation: { label: "配方", icon: "🧪", cls: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10" },
  claim: { label: "结论", icon: "💡", cls: "text-sky-300 border-sky-500/40 bg-sky-500/10" },
  source: { label: "文献", icon: "📄", cls: "text-amber-300 border-amber-500/40 bg-amber-500/10" },
  run: { label: "实验", icon: "⚗️", cls: "text-violet-300 border-violet-500/40 bg-violet-500/10" },
  artifact: { label: "工件", icon: "📦", cls: "text-slate-300 border-edge bg-ink/30" },
};

function typeMeta(t: string) {
  return TYPE_META[t] ?? TYPE_META.artifact;
}

function shortId(id: string): string {
  return id.length > 16 ? `${id.slice(0, 12)}…` : id;
}

function NodeBadge({ nodeType, nodeId }: { nodeType: string; nodeId: string }) {
  const meta = typeMeta(nodeType);
  return (
    <span
      className={`inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border ${meta.cls}`}
      title={`${nodeType}:${nodeId}`}
    >
      <span>{meta.icon}</span>
      <span className="font-medium">{meta.label}</span>
      <span className="font-mono text-[10px] opacity-80">{shortId(nodeId)}</span>
    </span>
  );
}

export default function ProvenanceLineage({
  nodeType,
  nodeId,
  title,
  onClose,
}: {
  nodeType: string;
  nodeId: string;
  title?: string;
  onClose: () => void;
}) {
  const [edges, setEdges] = useState<ProvenanceEdge[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setEdges(null);
    setError("");
    api
      .getProvenanceLineage(nodeType, nodeId)
      .then((res) => {
        if (!cancelled) setEdges(Array.isArray(res?.edges) ? res.edges : []);
      })
      .catch((e) => {
        if (!cancelled) setError(formatApiError(e));
      });
    return () => {
      cancelled = true;
    };
  }, [nodeType, nodeId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
      data-testid="provenance-lineage"
    >
      <div
        className="bg-panel border border-edge rounded-lg shadow-xl w-[34rem] max-w-[92vw] p-4 text-sm"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-slate-200">
            🔗 证据谱系{title ? ` · ${title.slice(0, 30)}` : ""}
          </h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        {edges === null && !error ? (
          <p className="text-xs text-slate-500">谱系加载中…</p>
        ) : error ? (
          <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2 text-xs">
            {error}
          </div>
        ) : !edges || edges.length === 0 ? (
          <p className="text-xs text-slate-500" data-testid="prov-empty">
            暂无谱系记录（该节点尚未关联文献结论或实验运行）
          </p>
        ) : (
          <div className="space-y-0 max-h-[60vh] overflow-auto pr-1">
            {edges.map((e, i) => (
              <div key={`${e.from_type}:${e.from_id}:${e.to_type}:${e.to_id}:${e.relation}:${i}`}>
                {i > 0 && (
                  <div className="text-center text-slate-500 text-[10px] py-0.5">▲ 上游</div>
                )}
                <div
                  className="border border-edge/60 bg-ink/20 rounded px-3 py-2 flex items-center gap-2 flex-wrap"
                  data-testid={`prov-edge-${i}`}
                >
                  <NodeBadge nodeType={e.from_type} nodeId={e.from_id} />
                  <span className="text-[10px] text-slate-500 font-mono">
                    —{e.relation}→
                  </span>
                  <NodeBadge nodeType={e.to_type} nodeId={e.to_id} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
