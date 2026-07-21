import { useEffect, useState } from "react";
import {
  api,
  formatApiError,
  type KGEntityResolveResponse,
  type KGRelationView,
  type KGSubstituteDiscoverResponse,
} from "../api";

const RELATION_LABELS: Record<string, string> = {
  substitutes: "替代",
  synergizes: "协同",
  inhibits: "抑制",
  correlates_pos: "正相关",
  correlates_neg: "负相关",
  requires: "依赖",
};

function relationLabel(type: string): string {
  return RELATION_LABELS[type] ?? type;
}

function RelationRow({ rel }: { rel: KGRelationView }) {
  const evidence = rel.evidence[0];
  return (
    <li className="text-[10px] text-slate-400 leading-relaxed border-l border-violet-500/30 pl-2">
      <span className="text-violet-300">{relationLabel(rel.relation_type)}</span>
      <span className="text-slate-500 mx-1">·</span>
      <span className="font-mono text-slate-500">{rel.source_entity_id.slice(0, 18)}</span>
      <span className="text-slate-600 mx-0.5">→</span>
      <span className="font-mono text-slate-500">{rel.target_entity_id.slice(0, 18)}</span>
      {evidence?.sentence && (
        <p className="mt-0.5 text-slate-500 italic truncate" title={evidence.sentence}>
          “{evidence.sentence}”
        </p>
      )}
    </li>
  );
}

export default function KgRelationPanel({ query }: { query: string }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolved, setResolved] = useState<KGEntityResolveResponse | null>(null);
  const [substitutes, setSubstitutes] = useState<KGSubstituteDiscoverResponse | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResolved(null);
      setSubstitutes(null);
      setError(null);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void (async () => {
        try {
          const resolveResp = await api.kgResolve(q);
          if (cancelled) return;
          setResolved(resolveResp);
          const primaryId =
            resolveResp.chemicals[0]?.id ?? resolveResp.trade_products[0]?.id ?? null;
          if (primaryId) {
            const subResp = await api.kgSubstitutes({ entityId: primaryId, limit: 5 });
            if (!cancelled) setSubstitutes(subResp);
          } else {
            setSubstitutes(null);
          }
        } catch (err) {
          if (!cancelled) {
            const msg = formatApiError(err);
            if (!msg.includes("409") && !msg.includes("知识图谱未启用")) {
              setError(msg);
            }
            setResolved(null);
            setSubstitutes(null);
          }
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }, 600);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  const relations = resolved?.top_relations ?? [];
  const hasContent =
    loading ||
    error ||
    (resolved && (resolved.chemicals.length > 0 || resolved.trade_products.length > 0));

  if (!hasContent) return null;

  const primary =
    resolved?.chemicals[0]?.canonical_name ??
    resolved?.trade_products[0]?.trade_name ??
    query.trim();

  return (
    <div className="shrink-0 rounded-lg border border-violet-500/25 bg-violet-500/5 px-3 py-2 text-[11px]">
      <button
        type="button"
        onClick={() => setExpanded((o) => !o)}
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <span className="text-violet-300 uppercase tracking-widest">
          🕸 知识图谱关系
          {loading && <span className="text-slate-500 normal-case ml-1">加载中…</span>}
        </span>
        <span className="text-slate-500 shrink-0">{expanded ? "▴" : "▾"}</span>
      </button>

      {error && (
        <p className="mt-1 text-rose-300/90 text-[10px]">{error}</p>
      )}

      {!expanded && !loading && relations.length > 0 && (
        <p className="mt-1 text-slate-400">
          {primary}：{relations.length} 条语义关系
          {substitutes && substitutes.substitutes.length > 0 && (
            <span> · {substitutes.substitutes.length} 个替代品候选</span>
          )}
        </p>
      )}

      {expanded && resolved && (
        <div className="mt-2 space-y-2 border-t border-edge/40 pt-2">
          <div className="text-slate-400">
            实体：<span className="text-slate-200">{primary}</span>
            {resolved.chemicals[0]?.cas_no && (
              <span className="ml-1 font-mono text-teal-300/80">{resolved.chemicals[0].cas_no}</span>
            )}
          </div>

          {relations.length > 0 ? (
            <div>
              <div className="text-violet-300/90 font-medium mb-1">语义关系 ({relations.length})</div>
              <ul className="space-y-1 max-h-28 overflow-y-auto">
                {relations.map((rel) => (
                  <RelationRow key={rel.id} rel={rel} />
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-slate-500">暂无已抽取的语义关系（需开启 KG 关系抽取并重建链接）。</p>
          )}

          {substitutes && substitutes.substitutes.length > 0 && (
            <div>
              <div className="text-teal-300/90 font-medium mb-1">替代品发现</div>
              <ul className="space-y-1 max-h-24 overflow-y-auto text-slate-400">
                {substitutes.substitutes.map((c) => (
                  <li key={c.entity_id}>
                    <span className="text-teal-200">{c.entity_name || c.entity_id}</span>
                    <span className="text-slate-600 ml-1">
                      ({relationLabel(c.relation_type)}, {Math.round(c.confidence * 100)}%)
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
