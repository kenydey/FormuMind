import { useEffect, useState } from "react";
import { api, type ObjectiveSpec, type WorkbenchRow } from "../api";
import { specText, valueMeetsSpec } from "../utils/workbenchColumns";

export type RowDetailAction =
  | "versions"
  | "attachments"
  | "qc"
  | "lineage"
  | "note"
  | "tags";

interface RowDetailBarProps {
  row: WorkbenchRow;
  objectives: ObjectiveSpec[];
  attachmentCount: number;
  onAction: (action: RowDetailAction) => void;
  onClose: () => void;
}

/**
 * P2: persistent detail strip for the selected workbench row — plan vs actual
 * diff, measurement vs spec verdict, and one-click doors to every DataLab-backed
 * action (versions / attachments / QC report / lineage). Replaces the silent
 * (Community-ineffective) master/detail panel.
 */
export default function RowDetailBar({
  row,
  objectives,
  attachmentCount,
  onAction,
  onClose,
}: RowDetailBarProps) {
  const [versionCount, setVersionCount] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (row.refcode) {
      api
        .getWorkbenchVersions(row.campaign_id, row.id)
        .then((res) => {
          if (!cancelled) setVersionCount(res.versions?.length ?? 0);
        })
        .catch(() => {
          if (!cancelled) setVersionCount(null);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [row.campaign_id, row.id, row.refcode]);

  const factorKeys = Array.from(
    new Set([
      ...Object.keys(row.planned_params ?? {}),
      ...Object.keys(row.actual_params ?? {}),
    ])
  ).slice(0, 10);
  const measuredObjectives = objectives.filter((o) => row.measurements?.[o.metric] != null);
  const specSummary = measuredObjectives
    .map((o) => {
      const pass = valueMeetsSpec(o, row.measurements?.[o.metric]);
      return { o, pass };
    })
    .filter((x) => x.pass !== null);

  const actionBtn =
    "text-[10px] border border-edge rounded px-1.5 py-1 hover:bg-ink/30 text-slate-300 whitespace-nowrap";

  return (
    <div className="border-t border-edge/40 bg-ink/20 px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="text-[10px] text-slate-400 uppercase tracking-wide">
          行 #{row.id} 详情
          {row.status && (
            <span className="ml-2 text-slate-500 normal-case">
              状态：{row.status}
            </span>
          )}
          {row.ingested && (
            <span className="ml-2 text-emerald-400 normal-case">✓ 已入训练</span>
          )}
          {row.refcode && (
            <span className="ml-2 text-slate-600 normal-case">{row.refcode}</span>
          )}
        </span>
        <button
          onClick={onClose}
          className="text-slate-500 hover:text-slate-300"
          aria-label="收起详情"
        >
          ✕
        </button>
      </div>

      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {factorKeys.length > 0 && (
          <div className="min-w-[14rem]">
            <div className="text-[9px] text-slate-500 mb-0.5">计划 vs 实际（黄=已偏离）</div>
            {factorKeys.map((k) => {
              const p = row.planned_params?.[k];
              const a = row.actual_params?.[k];
              const diff = p != null && a != null && Number(p) !== Number(a);
              return (
                <div key={k} className="flex gap-1.5 items-center text-[11px]">
                  <span className="text-slate-500 truncate max-w-[9rem]">{k}</span>
                  <span className="text-slate-400">{p ?? "—"}</span>
                  <span className="text-slate-600">→</span>
                  <span className={diff ? "text-amber-300 font-semibold" : "text-slate-300"}>
                    {a ?? "—"}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {specSummary.length > 0 && (
          <div className="min-w-[12rem]">
            <div className="text-[9px] text-slate-500 mb-0.5">测量 vs 规格</div>
            {specSummary.map(({ o, pass }) => (
              <div key={o.metric} className="flex gap-1.5 items-center text-[11px]">
                <span className={pass ? "text-emerald-300" : "text-red-300"}>
                  {pass ? "●" : "○"}
                </span>
                <span className="text-slate-400 truncate max-w-[8rem]">
                  {o.display_name || o.metric}
                </span>
                <span className="text-slate-300">
                  {String(row.measurements?.[o.metric])}
                </span>
                <span className="text-slate-600">{specText(o)}</span>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-start gap-1.5 flex-wrap">
          <span className="text-[9px] text-slate-500 w-full">DataLab 操作</span>
          <button className={actionBtn} onClick={() => onAction("versions")}>
            🕘 版本 {versionCount != null ? `(${versionCount})` : ""}
          </button>
          <button className={actionBtn} onClick={() => onAction("attachments")}>
            📎 附件 ({attachmentCount})
          </button>
          <button className={actionBtn} onClick={() => onAction("qc")}>
            📄 检测报告
          </button>
          <button className={actionBtn} onClick={() => onAction("lineage")}>
            🧬 谱系
          </button>
          <button className={actionBtn} onClick={() => onAction("note")}>
            ✏️ 备注
          </button>
          <button className={actionBtn} onClick={() => onAction("tags")}>
            🏷️ 标签
          </button>
        </div>
      </div>
    </div>
  );
}
