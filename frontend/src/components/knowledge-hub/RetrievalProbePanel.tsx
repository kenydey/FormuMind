import { useCallback, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  api,
  formatApiError,
  type KbGoldenEvalResponse,
  type KbQueryTestHit,
  type KbQueryTestMode,
  type KbQueryTestResponse,
} from "../../api";
import { useStore } from "../../store";

const SAMPLE_QUERIES = ["硅烷偶联剂", "磷化液", "钝化膜 铬酸盐", "水性环氧 盐雾"] as const;

type ScopeMode = "project" | "project_global" | "global";

function fmtScore(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(3);
}

function rankDelta(hit: KbQueryTestHit): string {
  if (hit.rank_before_rerank == null || hit.rerank_score == null) return "—";
  const d = hit.rank_before_rerank - hit.rank;
  if (d > 0) return `↑${d}`;
  if (d < 0) return `↓${Math.abs(d)}`;
  return "·";
}

/** Knowledge Hub · 检索探针 — scored multi-recall workbench. */
export default function RetrievalProbePanel({ active }: { active: boolean }) {
  const activeProjectId = useStore(useShallow((s) => s.activeProjectId));
  const [query, setQuery] = useState("硅烷偶联剂");
  const [mode, setMode] = useState<KbQueryTestMode>("hybrid");
  const [topK, setTopK] = useState(10);
  const [alpha, setAlpha] = useState(0.3);
  const [scope, setScope] = useState<ScopeMode>("project_global");
  const [busy, setBusy] = useState(false);
  const [goldenBusy, setGoldenBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<KbQueryTestResponse | null>(null);
  const [selected, setSelected] = useState<KbQueryTestHit | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [golden, setGolden] = useState<KbGoldenEvalResponse | null>(null);

  const scopeParams = useMemo(() => {
    if (!activeProjectId || scope === "global") {
      return { project_id: null as string | null, include_global: true };
    }
    if (scope === "project") {
      return { project_id: activeProjectId, include_global: false };
    }
    return { project_id: activeProjectId, include_global: true };
  }, [activeProjectId, scope]);

  const runProbe = useCallback(async () => {
    const q = query.trim();
    if (!q) {
      setError("请先输入检索词");
      return;
    }
    setBusy(true);
    setError(null);
    setGolden(null);
    try {
      const payload = await api.kbQueryTest({
        query: q,
        mode,
        top_k: topK,
        alpha,
        project_id: scopeParams.project_id,
        include_global: scopeParams.include_global,
        rerank: mode === "hybrid_rerank" ? true : null,
      });
      setResult(payload);
      setSelected(payload.hits?.[0] ?? null);
    } catch (e) {
      setResult(null);
      setSelected(null);
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }, [query, mode, topK, alpha, scopeParams]);

  const runGolden = useCallback(async () => {
    setGoldenBusy(true);
    setError(null);
    try {
      const payload = await api.kbGoldenEvalRun({
        mode: mode === "keyword" ? "hybrid" : mode,
        top_k: Math.min(topK, 5),
        alpha,
        project_id: scopeParams.project_id,
        include_global: scopeParams.include_global,
        rerank: mode === "hybrid_rerank" ? true : null,
      });
      setGolden(payload);
    } catch (e) {
      setGolden(null);
      setError(formatApiError(e));
    } finally {
      setGoldenBusy(false);
    }
  }, [mode, topK, alpha, scopeParams]);

  if (!active) return null;

  return (
    <div
      className="flex flex-col gap-3 h-full min-h-0 overflow-y-auto"
      data-testid="hub-retrieval-pane"
    >
      <div className="text-[10px] text-slate-500 border border-edge/50 rounded px-2 py-1">
        KB Chunk 召回探针 · POST /api/kb/query-test
        {activeProjectId ? (
          <span className="ml-2 text-slate-400">项目 {activeProjectId.slice(0, 8)}…</span>
        ) : (
          <span className="ml-2 text-amber-300/90">未选项目 → 按全局语料检索</span>
        )}
        <span className="block mt-0.5 text-slate-600">
          Wiki FTS / 结构化学检索不在本页；α=0≈向量、α=1≈BM25。
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5 items-center">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void runProbe();
          }}
          placeholder="输入化学专业 Query…"
          className="flex-1 min-w-[12rem] bg-ink border border-edge rounded px-2 py-1.5 text-xs"
          data-testid="retrieval-probe-input"
        />
        <button
          type="button"
          disabled={busy || !query.trim()}
          onClick={() => void runProbe()}
          className="text-xs px-3 py-1.5 rounded border border-accent/50 text-accent disabled:opacity-40"
          data-testid="retrieval-probe-run"
        >
          {busy ? "运行中…" : "运行"}
        </button>
        <button
          type="button"
          disabled={goldenBusy}
          onClick={() => void runGolden()}
          className="text-xs px-3 py-1.5 rounded border border-teal-500/40 text-teal-300 disabled:opacity-40"
          data-testid="retrieval-probe-golden"
        >
          {goldenBusy ? "批跑中…" : "Golden 批跑"}
        </button>
      </div>

      <div className="flex flex-wrap gap-1">
        {SAMPLE_QUERIES.map((q) => (
          <button
            key={q}
            type="button"
            className="text-[10px] px-2 py-0.5 rounded border border-edge/60 text-slate-400 hover:border-accent/40"
            onClick={() => setQuery(q)}
            data-testid={`retrieval-chip-${q}`}
          >
            {q}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-[11px]">
        <label className="flex flex-col gap-1">
          <span className="text-slate-500">模式</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as KbQueryTestMode)}
            className="bg-ink border border-edge rounded px-2 py-1"
            data-testid="retrieval-probe-mode"
          >
            <option value="keyword">关键词</option>
            <option value="hybrid">混合</option>
            <option value="hybrid_rerank">混合+重排</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-slate-500">作用域</span>
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as ScopeMode)}
            className="bg-ink border border-edge rounded px-2 py-1"
            data-testid="retrieval-probe-scope"
          >
            <option value="project_global">项目+全局</option>
            <option value="project">仅当前项目</option>
            <option value="global">全局</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-slate-500">top_k</span>
          <input
            type="number"
            min={1}
            max={50}
            value={topK}
            onChange={(e) => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 10)))}
            className="bg-ink border border-edge rounded px-2 py-1"
            data-testid="retrieval-probe-topk"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-slate-500">alpha (BM25)</span>
          <input
            type="number"
            min={0}
            max={1}
            step={0.1}
            value={alpha}
            onChange={(e) => setAlpha(Math.max(0, Math.min(1, Number(e.target.value) || 0)))}
            className="bg-ink border border-edge rounded px-2 py-1"
            data-testid="retrieval-probe-alpha"
          />
        </label>
      </div>

      {error && (
        <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">{error}</div>
      )}
      {result?.warning && (
        <div className="text-xs text-amber-200 border border-amber-500/30 rounded px-2 py-1">
          {result.warning}
        </div>
      )}

      {result && (
        <div className="text-[10px] text-slate-500 flex flex-wrap gap-3">
          <span>
            vector_mode=<span className="font-mono text-slate-300">{result.vector_mode}</span>
          </span>
          <span>
            elapsed=<span className="font-mono text-slate-300">{result.elapsed_ms}ms</span>
          </span>
          <span>
            rerank=
            <span className="font-mono text-slate-300">
              {result.params.rerank_applied ? "applied" : "off"}
            </span>
          </span>
          <label className="ml-auto flex items-center gap-1 cursor-pointer">
            <input
              type="checkbox"
              checked={showJson}
              onChange={(e) => setShowJson(e.target.checked)}
            />
            原始 JSON
          </label>
        </div>
      )}

      {result && !showJson && (
        <div className="border border-edge/50 rounded overflow-hidden" data-testid="retrieval-probe-table">
          <div className="overflow-x-auto max-h-56">
            <table className="w-full text-[10px]">
              <thead className="bg-ink/80 text-slate-500 sticky top-0">
                <tr>
                  <th className="text-left px-2 py-1">#</th>
                  <th className="text-left px-2 py-1">source / title</th>
                  <th className="text-right px-2 py-1 font-mono">bm25</th>
                  <th className="text-right px-2 py-1 font-mono">cosine</th>
                  <th className="text-right px-2 py-1 font-mono">hybrid</th>
                  <th className="text-right px-2 py-1 font-mono">rerank</th>
                  <th className="text-right px-2 py-1">Δ</th>
                </tr>
              </thead>
              <tbody>
                {(result.hits || []).length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-2 py-3 text-slate-600 text-center">
                      无命中
                    </td>
                  </tr>
                ) : (
                  result.hits.map((h) => {
                    const activeRow = selected?.rank === h.rank;
                    return (
                      <tr
                        key={`${h.rank}-${h.chunk_id || h.source_id || h.title}`}
                        className={`border-t border-edge/30 cursor-pointer ${
                          activeRow ? "bg-accent/10" : "hover:bg-ink/60"
                        }`}
                        onClick={() => setSelected(h)}
                      >
                        <td className="px-2 py-1 text-slate-400">{h.rank}</td>
                        <td className="px-2 py-1 text-slate-200 truncate max-w-[14rem]" title={h.title}>
                          {h.title || h.source_id || "—"}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-slate-400">
                          {fmtScore(h.bm25_score)}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-slate-400">
                          {fmtScore(h.cosine_score)}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-slate-300">
                          {fmtScore(h.hybrid_score ?? h.relevance)}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-teal-300/90">
                          {fmtScore(h.rerank_score)}
                        </td>
                        <td className="px-2 py-1 text-right text-slate-400">{rankDelta(h)}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          {selected && (
            <div
              className="border-t border-edge/40 px-3 py-2 text-[11px] text-slate-400 bg-ink/40"
              data-testid="retrieval-probe-snippet"
            >
              <div className="text-slate-300 mb-1">{selected.title}</div>
              <p className="whitespace-pre-wrap leading-relaxed">{selected.snippet || "（无 snippet）"}</p>
            </div>
          )}
        </div>
      )}

      {result && showJson && (
        <pre className="text-[10px] bg-ink/50 border border-edge/40 rounded p-2 overflow-auto max-h-64 text-slate-400">
          {JSON.stringify(result, null, 2)}
        </pre>
      )}

      {golden && (
        <div
          className="border border-edge/50 rounded p-2 space-y-1"
          data-testid="retrieval-probe-golden-results"
        >
          <div className="text-[11px] text-slate-300">
            Golden 批跑 · {golden.passed}/{golden.total} 通过
            <span className="text-slate-500 ml-2">mode={golden.mode}</span>
          </div>
          <div className="max-h-40 overflow-auto space-y-1">
            {golden.results.map((row) => (
              <div
                key={row.question}
                className={`text-[10px] rounded px-2 py-1 border ${
                  row.passed
                    ? "border-emerald-500/30 text-emerald-200/90"
                    : "border-rose-500/30 text-rose-200/90"
                }`}
              >
                <span className="font-medium">{row.passed ? "PASS" : "FAIL"}</span>
                <span className="ml-2 text-slate-300">{row.question}</span>
                {row.matched_keyword && (
                  <span className="ml-2 text-slate-500">hit={row.matched_keyword}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
