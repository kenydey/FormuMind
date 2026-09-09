import { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "../../api";

/** Weak graph pane: SQLite KG stats + optional Neo4j browse. */
export default function HubGraphPane({ active }: { active: boolean }) {
  const [kg, setKg] = useState<Record<string, unknown> | null>(null);
  const [neo, setNeo] = useState<Record<string, unknown> | null>(null);
  const [compounds, setCompounds] = useState<
    { uid?: string; name?: string | null; cas_number?: string | null }[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const stats = await api.kgStats();
      setKg(stats as unknown as Record<string, unknown>);
    } catch (e) {
      setKg(null);
      setError(formatApiError(e));
    }
    try {
      const ns = await api.neo4jStats();
      setNeo(ns as unknown as Record<string, unknown>);
      const rows = await api.neo4jCompounds("", 40);
      setCompounds(Array.isArray(rows) ? rows : []);
    } catch {
      setNeo(null);
      setCompounds([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  return (
    <div className="flex flex-col gap-3 h-full min-h-0 overflow-y-auto" data-testid="hub-graph-pane">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="text-xs px-2 py-1 border border-edge rounded"
          onClick={() => void refresh()}
        >
          刷新
        </button>
        {loading && <span className="text-[11px] text-slate-500">加载中…</span>}
      </div>
      {error && (
        <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">{error}</div>
      )}
      <section className="border border-edge/60 rounded p-3 text-xs space-y-1">
        <h3 className="text-sm text-slate-200 mb-1">SQLite 知识图谱</h3>
        {kg ? (
          <pre className="text-slate-400 whitespace-pre-wrap font-mono text-[11px]">
            {JSON.stringify(kg, null, 2)}
          </pre>
        ) : (
          <p className="text-slate-500">无法读取 /api/kg/stats（可能未启用 kg_enabled）</p>
        )}
      </section>
      <section className="border border-edge/60 rounded p-3 text-xs space-y-2">
        <h3 className="text-sm text-slate-200 mb-1">Neo4j（可选）</h3>
        {neo ? (
          <>
            <pre className="text-slate-400 whitespace-pre-wrap font-mono text-[11px]">
              {JSON.stringify(neo, null, 2)}
            </pre>
            {compounds.length > 0 && (
              <table className="w-full text-left mt-2">
                <thead className="text-slate-500">
                  <tr>
                    <th className="py-1">名称</th>
                    <th>CAS</th>
                    <th>uid</th>
                  </tr>
                </thead>
                <tbody>
                  {compounds.slice(0, 30).map((c, i) => (
                    <tr key={c.uid || i} className="border-t border-edge/40 text-slate-300">
                      <td className="py-1">{c.name || "—"}</td>
                      <td>{c.cas_number || "—"}</td>
                      <td className="font-mono text-[10px]">{c.uid || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : (
          <p className="text-slate-500">
            Neo4j 未启用或不可达。可在设置中打开 <code>neo4j_enabled</code>；Hub
            不做完整图可视化编辑器。
          </p>
        )}
      </section>
    </div>
  );
}
