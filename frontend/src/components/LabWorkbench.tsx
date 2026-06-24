import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type {
  ColDef,
  ICellRendererParams,
  ValueParserParams,
  ValueSetterParams,
} from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import { api, primaryObjectiveMetric } from "../api";
import type { DOEPlan, Requirement, WorkbenchRow } from "../api";

interface LabWorkbenchProps {
  campaignId: number;
  doePlan: DOEPlan;
  requirement: Requirement;
  onSaved?: (rows: WorkbenchRow[]) => void;
}

const RATING_OPTIONS = ["A", "B", "C", "D", "pass", "fail"];

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

function numericParser(params: ValueParserParams) {
  const raw = String(params.newValue ?? "").trim().replace(/\s+/g, "");
  if (raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function isRatingField(key: string): boolean {
  return /rating|grade|level/i.test(key) && !/hours|efficiency|temp/i.test(key);
}

export default function LabWorkbench({ campaignId, doePlan, requirement, onSaved }: LabWorkbenchProps) {
  const gridRef = useRef<AgGridReact<WorkbenchRow>>(null);
  const [rows, setRows] = useState<WorkbenchRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const metric = primaryObjectiveMetric(requirement);
  const factorKeys = useMemo(() => {
    if (doePlan.factors.length > 0) return doePlan.factors.map((f) => f.name);
    const first = rows[0]?.planned_params ?? {};
    return Object.keys(first);
  }, [doePlan.factors, rows]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.getWorkbenchCampaign(campaignId);
        if (!cancelled) setRows(data.rows);
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
    const cols: ColDef<WorkbenchRow>[] = [
      { field: "id", headerName: "ID", editable: false, width: 64, pinned: "left" },
      {
        field: "status",
        headerName: "状态",
        editable: false,
        width: 96,
        cellRenderer: StatusCellRenderer,
      },
    ];

    for (const key of factorKeys) {
      const short = key.replace(" (DGEBA)", "").slice(0, 12);
      cols.push({
        colId: `planned_${key}`,
        headerName: `计划 ${short}`,
        editable: false,
        valueGetter: (p) => p.data?.planned_params?.[key],
        cellStyle: { backgroundColor: "#f3f4f6", color: "#374151" },
        width: 108,
      });
      cols.push({
        colId: `actual_${key}`,
        headerName: `实际 ${short}`,
        editable: true,
        valueGetter: (p) => p.data?.actual_params?.[key],
        valueSetter: (p: ValueSetterParams<WorkbenchRow>) => {
          if (!p.data) return false;
          p.data.actual_params = { ...p.data.actual_params, [key]: p.newValue as number };
          return true;
        },
        valueParser: numericParser,
        cellStyle: { backgroundColor: "#eff6ff", color: "#1e40af" },
        width: 108,
      });
    }

    cols.push({
      colId: `meas_${metric}`,
      headerName: `实测 ${metric}`,
      editable: true,
      valueGetter: (p) => p.data?.measurements?.[metric],
      valueSetter: (p: ValueSetterParams<WorkbenchRow>) => {
        if (!p.data) return false;
        p.data.measurements = { ...p.data.measurements, [metric]: p.newValue as number | string };
        return true;
      },
      valueParser: isRatingField(metric) ? undefined : numericParser,
      cellEditor: isRatingField(metric) ? "agSelectCellEditor" : undefined,
      cellEditorParams: isRatingField(metric) ? { values: RATING_OPTIONS } : undefined,
      cellStyle: { backgroundColor: "#fefce8", color: "#713f12" },
      flex: 1,
      minWidth: 120,
    });

    return cols;
  }, [factorKeys, metric]);

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
      const res = await api.syncWorkbench({
        campaign_id: campaignId,
        rows: allRows.map((r) => ({
          id: r.id,
          status: r.status,
          actual_params: r.actual_params ?? {},
          measurements: r.measurements ?? {},
        })),
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

  return (
    <div className="shadow-sm rounded-lg border border-gray-200 dark:border-edge overflow-hidden bg-panel/30">
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
          {rows.filter((r) => r.status === "Completed").length}/{rows.length} 已完成 · 支持 Excel 粘贴
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
