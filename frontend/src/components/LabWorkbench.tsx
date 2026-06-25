import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import { api } from "../api";
import type { DOEPlan, Requirement, WorkbenchRow } from "../api";
import { useStore } from "../store";
import {
  buildWorkbenchColumnDefs,
  effectiveObjectives,
  factorKeysFromPlan,
} from "../utils/workbenchColumns";

interface LabWorkbenchProps {
  campaignId: number;
  doePlan: DOEPlan;
  requirement: Requirement;
  onSaved?: (rows: WorkbenchRow[]) => void;
}

function StatusBadge({ value }: { value: string }) {
  const tone =
    value === "Completed"
      ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
      : "bg-amber-500/20 text-amber-300 border-amber-500/40";
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-medium ${tone}`}>
      {value || "Pending"}
    </span>
  );
}

function StatusCellRenderer(props: ICellRendererParams) {
  return <StatusBadge value={String(props.value ?? "Pending")} />;
}

export default function LabWorkbench({
  campaignId,
  doePlan,
  requirement,
  onSaved,
}: LabWorkbenchProps) {
  const gridRef = useRef<AgGridReact<WorkbenchRow>>(null);
  const [rows, setRows] = useState<WorkbenchRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiSnapshot, setApiSnapshot] = useState<ReturnType<typeof effectiveObjectives> | undefined>();

  const workbenchObjectivesSnapshot = useStore((s) => s.workbenchObjectivesSnapshot);

  const objectives = useMemo(
    () =>
      effectiveObjectives(
        requirement.objectives ?? [],
        workbenchObjectivesSnapshot ?? apiSnapshot
      ),
    [requirement.objectives, workbenchObjectivesSnapshot, apiSnapshot]
  );

  const factorKeys = useMemo(() => factorKeysFromPlan(doePlan, rows), [doePlan, rows]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.getWorkbenchCampaign(campaignId);
        if (!cancelled) {
          setRows(data.rows);
          if (data.objectives_snapshot?.length) {
            setApiSnapshot(data.objectives_snapshot);
          }
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  const columnDefs = useMemo<ColDef<WorkbenchRow>[]>(() => {
    const cols = buildWorkbenchColumnDefs(factorKeys, objectives);
    const statusCol = cols.find((c) => c.field === "status");
    if (statusCol) statusCol.cellRenderer = StatusCellRenderer;
    return cols;
  }, [factorKeys, objectives]);

  const defaultColDef = useMemo<ColDef>(
    () => ({
      sortable: false,
      filter: false,
      resizable: true,
      suppressMovable: true,
    }),
    []
  );

  const processCellFromClipboard = useCallback((params: { value: unknown }) => {
    if (params.value == null) return params.value;
    return String(params.value).trim();
  }, []);

  const handleSave = async () => {
    gridRef.current?.api.stopEditing();
    const allRows: WorkbenchRow[] = [];
    gridRef.current?.api.forEachNode((node) => {
      if (node.data) allRows.push(node.data);
    });
    if (allRows.length === 0) {
      setError("台账为空");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const syncMetrics = objectives.map((o) => o.metric);
      const res = await api.syncWorkbench({
        campaign_id: campaignId,
        rows: allRows.map((r) => {
          const measurements: Record<string, number | string> = {};
          for (const m of syncMetrics) {
            const v = r.measurements?.[m];
            if (v !== undefined && v !== null && v !== "") {
              measurements[m] = v as number | string;
            }
          }
          return {
            id: r.id,
            status: r.status,
            actual_params: r.actual_params ?? {},
            measurements,
          };
        }),
      });
      setRows(res.rows);
      onSaved?.(res.rows);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <p className="text-xs text-slate-500 py-3">加载实验台账…</p>;
  }

  const frozen = workbenchObjectivesSnapshot ?? apiSnapshot;

  return (
    <div className="shadow-sm rounded-lg border border-gray-200 dark:border-edge overflow-hidden bg-panel/30">
      {frozen && frozen.length > 0 && (
        <div className="px-2 py-1.5 border-b border-edge/30 bg-ink/30 text-[10px] text-slate-500">
          本 Campaign 指标已冻结（键 = metric）：
          {frozen.map((o) => (
            <span key={o.metric} className="ml-2 font-mono text-accent2">
              {o.display_name || o.metric}
            </span>
          ))}
        </div>
      )}
      {error && <p className="text-[11px] text-red-400 px-2 py-1 border-b border-edge/30">{error}</p>}
      <div className="ag-theme-alpine-dark w-full" style={{ height: 280 }}>
        <AgGridReact<WorkbenchRow>
          ref={gridRef}
          rowData={rows}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          enableCellTextSelection={true}
          ensureDomOrder={true}
          processCellFromClipboard={processCellFromClipboard}
          stopEditingWhenCellsLoseFocus={true}
        />
      </div>
      <div className="flex items-center justify-between gap-2 px-2 py-2 border-t border-edge/40 bg-ink/20">
        <span className="text-[10px] text-slate-500">
          {rows.filter((r) => r.status === "Completed").length}/{rows.length} 已完成 ·{" "}
          {objectives.length} 项指标 · 支持 Excel 粘贴
        </span>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="text-xs bg-accent2/90 hover:bg-accent2 text-ink font-semibold rounded px-3 py-1.5 disabled:opacity-40"
        >
          {saving ? "同步中…" : "保存台账并同步"}
        </button>
      </div>
    </div>
  );
}
