import { useCallback, useEffect, useState } from "react";
import {
  api,
  formatApiError,
  type Formulation,
  type FormulationVersionView,
  type VersionDiffResult,
} from "../api";
import FormulationLineageGraph from "./charts/FormulationLineageGraph";

/**
 * Revision history for one formulation.
 *
 * Snapshots on their own do not answer the question anyone actually asks, so
 * this leads with the diff between two revisions rather than a list of frozen
 * copies. The topology flag matters enough to call out separately:
 * re-balancing a recipe and swapping a component out are different kinds of
 * change to review.
 */

const CHANGE_LABEL: Record<string, string> = {
  added: "新增",
  removed: "移除",
  adjusted: "调整",
};

const CHANGE_STYLE: Record<string, string> = {
  added: "text-green-400",
  removed: "text-red-400",
  adjusted: "text-accent",
};

const METRIC_LABELS: Record<string, string> = {
  salt_spray_hours: "耐盐雾",
  cost_cny_per_kg: "成本",
  voc_gpl: "VOC",
  cleaning_efficiency: "清洗率",
};

export default function VersionHistoryModal({ form }: { form: Formulation }) {
  const [versions, setVersions] = useState<FormulationVersionView[]>([]);
  const [lineageId, setLineageId] = useState<string | null>(null);
  const [diff, setDiff] = useState<VersionDiffResult | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [viewMode, setViewMode] = useState<"list" | "graph">("list");

  const load = useCallback(async () => {
    try {
      const found = await api.findFormulationLineages(form.name, form.domain, 1);
      if (found.length) {
        setLineageId(found[0].lineage_id);
        setVersions(found[0].versions);
      } else {
        setLineageId(null);
        setVersions([]);
      }
    } catch (err) {
      setError(formatApiError(err));
    }
  }, [form.name, form.domain]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveVersion() {
    setBusy(true);
    setError("");
    try {
      const parent = versions.length ? versions[versions.length - 1].id : null;
      await api.saveFormulationVersion({
        formulation: form,
        lineage_id: lineageId,
        parent_version_id: parent,
        change_summary: note.trim(),
      });
      setNote("");
      await load();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  async function showDiff(fromId: string, toId: string) {
    setError("");
    setDiff(null);
    try {
      setDiff(await api.diffFormulationVersions(fromId, toId));
    } catch (err) {
      setError(formatApiError(err));
    }
  }

  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-400">
        每次保存都是一条不可变的修订记录。选相邻两版可以看到
        <span className="text-accent">具体改了哪些成分、指标怎么变</span>。
      </p>

      <div className="flex items-center gap-2">
        <input
          className="flex-1 bg-panel border border-edge rounded px-2 py-1"
          placeholder="本次修改说明（留空则自动生成）"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <button
          className="px-3 py-1.5 rounded bg-accent/20 border border-accent/40 text-accent
                     hover:bg-accent/30 disabled:opacity-50 whitespace-nowrap"
          onClick={saveVersion}
          disabled={busy}
        >
          {busy ? "保存中…" : "💾 保存为新版本"}
        </button>
      </div>

      {error && (
        <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2">
          {error}
        </div>
      )}

      {versions.length > 0 && (
        <div className="flex items-center gap-1 border border-edge rounded overflow-hidden w-fit">
          <button
            onClick={() => setViewMode("list")}
            className={`text-[11px] px-2.5 py-1 ${viewMode === "list" ? "bg-accent/20 text-accent" : "text-slate-400"}`}
          >
            列表
          </button>
          <button
            onClick={() => setViewMode("graph")}
            className={`text-[11px] px-2.5 py-1 ${viewMode === "graph" ? "bg-accent/20 text-accent" : "text-slate-400"}`}
          >
            图谱
          </button>
        </div>
      )}

      {versions.length === 0 ? (
        <div className="text-slate-500 text-xs">
          该配方尚无修订记录——保存一次即可开始记录谱系。
        </div>
      ) : viewMode === "graph" ? (
        <FormulationLineageGraph
          nodes={versions.map((v) => ({
            id: v.id,
            version: v.version,
            name: form.name,
            change_summary: v.change_summary || "无说明",
            snapshot: v.snapshot || {},
            parent_id: v.parent_version_id,
            children: versions.filter((child) => child.parent_version_id === v.id).map((child) => child.id),
          }))}
          onNodeClick={(node) => {
            const version = versions.find((v) => v.id === node.id);
            if (version) {
              const prev = versions.find((v2) => v2.version === version.version - 1);
              if (prev) showDiff(prev.id, version.id);
            }
          }}
        />
      ) : (
        <div className="space-y-1">
          {versions.map((v, i) => {
            const prev = versions[i - 1];
            return (
              <div
                key={v.id}
                className="flex items-start gap-2 border-t border-edge/50 py-1.5 text-xs"
              >
                <span className="text-accent font-medium w-10 shrink-0">v{v.version}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-slate-300 truncate">
                    {v.change_summary || "（无说明）"}
                  </div>
                  <div className="text-slate-500">
                    {v.created_by || "未署名"}
                    {v.created_at ? ` · ${new Date(v.created_at).toLocaleString()}` : ""}
                    {v.parent_version_id === null && v.version > 1 && (
                      <span className="text-yellow-400/70"> · 父版本已删除</span>
                    )}
                  </div>
                </div>
                {prev && (
                  <button
                    className="text-slate-400 hover:text-accent shrink-0"
                    onClick={() => showDiff(prev.id, v.id)}
                  >
                    对比 v{prev.version}→v{v.version}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {diff && (
        <div className="space-y-2 border-t border-edge pt-3">
          <div className="text-slate-300">
            v{diff.from_version} → v{diff.to_version}
            {diff.topology_changed ? (
              // Worth separating: swapping a component is a different review
              // from moving the ratios around.
              <span className="ml-2 text-xs text-amber-400">成分组成变化</span>
            ) : (
              <span className="ml-2 text-xs text-slate-500">仅配比调整</span>
            )}
          </div>
          {diff.renamed && (
            <div className="text-xs text-slate-400">
              重命名：{diff.renamed[0]} → {diff.renamed[1]}
            </div>
          )}

          {diff.ingredient_changes.length === 0 ? (
            <div className="text-xs text-slate-500">成分无变化</div>
          ) : (
            <table className="w-full text-xs">
              <tbody>
                {diff.ingredient_changes.map((c) => (
                  <tr key={`${c.name}-${c.change}`} className="border-t border-edge/40">
                    <td className={`py-1 w-12 ${CHANGE_STYLE[c.change]}`}>
                      {CHANGE_LABEL[c.change]}
                    </td>
                    <td className="text-slate-200">{c.name}</td>
                    <td className="text-right text-slate-400">
                      {c.before_pct ?? "—"} → {c.after_pct ?? "—"}
                    </td>
                    <td className="text-right w-20">
                      {c.delta_pct != null && (
                        <span className={c.delta_pct > 0 ? "text-green-400" : "text-red-400"}>
                          {c.delta_pct > 0 ? "+" : ""}
                          {c.delta_pct}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {Object.keys(diff.metric_deltas).length > 0 && (
            <div className="flex flex-wrap gap-3 text-xs">
              {Object.entries(diff.metric_deltas)
                .filter(([m, d]) => d.pct != null && METRIC_LABELS[m])
                .map(([metric, d]) => (
                  <span key={metric} className="text-slate-400">
                    {METRIC_LABELS[metric]}{" "}
                    <span className={(d.pct ?? 0) > 0 ? "text-green-400" : "text-red-400"}>
                      {(d.pct ?? 0) > 0 ? "+" : ""}
                      {d.pct?.toFixed(1)}%
                    </span>
                  </span>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
