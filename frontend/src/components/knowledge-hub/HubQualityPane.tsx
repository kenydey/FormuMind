import { useEffect, useState } from "react";
import { api, formatApiError, type KBQualityOps } from "../../api";
import { useStore } from "../../store";
import RetrievalProbePanel from "./RetrievalProbePanel";

/** Batch D: Hub quality ops — read-only aggregate of gate / scan / shadow / score. */
export default function HubQualityPane({ active }: { active: boolean }) {
  const projectId = useStore((s) => s.activeProjectId);
  const setTab = useStore((s) => s.setKnowledgeHubTab);
  const [data, setData] = useState<KBQualityOps | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const q = await api.kbQualityOps(projectId || undefined);
        if (!cancelled) setData(q);
      } catch (e) {
        if (!cancelled) setError(formatApiError(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [active, projectId]);

  const score = data?.kb_quality_score ?? null;
  const scoreTone =
    score == null
      ? "text-slate-400"
      : score >= 70
        ? "text-emerald-300"
        : score >= 45
          ? "text-amber-300"
          : "text-rose-300";

  return (
    <div className="h-full overflow-y-auto space-y-3 pr-1" data-testid="hub-quality-pane">
      <div className="rounded-lg border border-edge/60 bg-ink/30 p-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-sm text-slate-100">知识库质量运营</h3>
            <p className="text-[10px] text-slate-500 mt-0.5">
              只读聚合：scan 压力 · topicality 影子校准 · 闸计数 · 启发式评分（不改入库行为）
            </p>
          </div>
          <div className="text-right" data-testid="hub-quality-score">
            <div className={`text-2xl font-mono ${scoreTone}`}>
              {loading ? "…" : score == null ? "—" : score.toFixed(0)}
            </div>
            <div className="text-[9px] text-slate-500 uppercase tracking-wider">quality</div>
          </div>
        </div>
        {error && (
          <p className="text-[11px] text-rose-300 mt-2" data-testid="hub-quality-error">
            {error}
          </p>
        )}
        {data && (
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]">
            <Stat label="活跃源" value={data.sources_active} />
            <Stat label="已归档" value={data.sources_archived} />
            <Stat label="scan 压力" value={data.scan_pressure?.toFixed?.(3) ?? data.scan_pressure} warn={data.scan_near_cap} />
            <Stat label="向量模式" value={data.vector_mode} />
            <Stat
              label="主题拒收%"
              value={
                data.topicality_would_reject_pct == null
                  ? "—"
                  : `${data.topicality_would_reject_pct}`
              }
            />
            <Stat label="嵌入块" value={data.embedded_chunks} />
            <Stat label="活跃块" value={data.chunks_active} />
            <Stat label="shadow 批" value={(data.relevance_shadow as any)?.batch_count} />
          </div>
        )}
        {data?.scan_near_cap && (
          <div
            className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-200"
            data-testid="hub-quality-scan-cta"
          >
            <div className="font-medium">scan 接近上限</div>
            <p className="text-amber-200/80 mt-0.5">
              活跃切块接近 `kb_search_scan_limit`。建议：资料页归档低质源 → 依赖管理 dry-run retention purge（需确认，默认不物理删）。
            </p>
            <div className="flex flex-wrap gap-2 mt-1.5">
              <button
                type="button"
                className="border border-amber-500/40 rounded px-2 py-0.5 hover:bg-amber-500/20"
                data-testid="hub-quality-cta-materials"
                onClick={() => setTab("materials")}
              >
                打开资料页归档
              </button>
              <button
                type="button"
                className="border border-amber-500/40 rounded px-2 py-0.5 hover:bg-amber-500/20"
                data-testid="hub-quality-cta-retrieval"
                onClick={() => setTab("retrieval")}
              >
                查看检索探针
              </button>
            </div>
          </div>
        )}
        {data?.kb_quality_components && (
          <div className="mt-2 flex flex-wrap gap-1" data-testid="hub-quality-components">
            {Object.entries(data.kb_quality_components).map(([k, v]) => (
              <span
                key={k}
                className="text-[9px] px-1.5 py-0.5 rounded border border-edge/50 text-slate-400"
              >
                {k} <span className="font-mono text-slate-300">{Number(v).toFixed(1)}</span>
              </span>
            ))}
          </div>
        )}
        {data?.notes?.length ? (
          <ul className="mt-2 text-[10px] text-slate-500 list-disc pl-4 space-y-0.5">
            {data.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="rounded-lg border border-edge/60 p-2">
        <h4 className="text-[11px] text-slate-300 mb-1 px-1">Golden 检索探针 / MRR 趋势</h4>
        <RetrievalProbePanel active={active} />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  warn,
}: {
  label: string;
  value: unknown;
  warn?: boolean;
}) {
  return (
    <div
      className={`rounded border px-2 py-1.5 ${
        warn ? "border-amber-500/40 bg-amber-500/10" : "border-edge/50 bg-ink/40"
      }`}
    >
      <div className="text-[9px] text-slate-500">{label}</div>
      <div className={`font-mono text-xs ${warn ? "text-amber-300" : "text-slate-200"}`}>
        {value == null || value === "" ? "—" : String(value)}
      </div>
    </div>
  );
}
