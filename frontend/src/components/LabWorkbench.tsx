import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type { ColDef, ICellRendererParams, GetContextMenuItemsParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

ModuleRegistry.registerModules([AllCommunityModule]);

import { api } from "../api";
import type { DOEPlan, Requirement, WorkbenchRow } from "../api";
import { useStore } from "../store";
import {
  buildWorkbenchColumnDefs,
  effectiveObjectives,
  factorKeysFromPlan,
} from "../utils/workbenchColumns";
import NoteEditor from "./NoteEditor";
import TagPicker from "./TagPicker";

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
  const [saveHint, setSaveHint] = useState<string | null>(null);
  const [loopRoundCount, setLoopRoundCount] = useState(0);
  const [loopConverged, setLoopConverged] = useState(false);
  const [apiSnapshot, setApiSnapshot] = useState<ReturnType<typeof effectiveObjectives> | undefined>();

  // ── Phase 2 modal state ──────────────────────────────────────
  const [noteEditorRow, setNoteEditorRow] = useState<WorkbenchRow | null>(null);
  const [tagPickerRow, setTagPickerRow] = useState<WorkbenchRow | null>(null);

  const workbenchObjectivesSnapshot = useStore((s) => s.workbenchObjectivesSnapshot);
  const autoLoopOnSync = useStore((s) => s.autoLoopOnSync);
  const setAutoLoopOnSync = useStore((s) => s.setAutoLoopOnSync);
  const optimizeEngine = useStore((s) => s.optimizeEngine);
  const loopDoeEngine = useStore((s) => s.loopDoeEngine);
  const campaignState = useStore((s) => s.campaignState);
  const followLoopTask = useStore((s) => s.followLoopTask);
  const refreshWorkbenchStats = useStore((s) => s.refreshWorkbenchStats);

  const objectives = useMemo(
    () => effectiveObjectives(
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
            setApiSnapshot(data.objectives_snapshot as any);
          }
          const history = data.loop_history ?? [];
          setLoopRoundCount(history.length);
          setLoopConverged(Boolean(history.length && (history[history.length - 1] as any)?.converged));
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [campaignId]);

  const columnDefs = useMemo<ColDef<WorkbenchRow>[]>(() => {
    const cols = buildWorkbenchColumnDefs(factorKeys, objectives, doePlan);
    const statusCol = cols.find(
      (c): c is ColDef<WorkbenchRow> => "field" in c && c.field === "status"
    );
    if (statusCol) statusCol.cellRenderer = StatusCellRenderer;
    return cols as ColDef<WorkbenchRow>[];
  }, [factorKeys, objectives, doePlan]);

  const defaultColDef = useMemo<ColDef>(
    () => ({ sortable: false, filter: false, resizable: true, suppressMovable: true }),
    []
  );

  const processCellFromClipboard = useCallback(
    (params: { value: unknown }) => {
      if (params.value == null) return params.value;
      return String(params.value).trim();
    },
    []
  );

  // ── Phase 1.4: Excel export ──────────────────────────────────
  const handleExportExcel = useCallback(() => {
    const agApi = gridRef.current?.api;
    if (!agApi) return;
    agApi.exportDataAsExcel({
      fileName: `DOE-C${campaignId}-${new Date().toISOString().slice(0, 10)}.xlsx`,
      sheetName: "实验台账",
      processCellCallback: (p) => p.value ?? "",
    });
  }, [campaignId]);

  // ── Phase 2: context menu ────────────────────────────────────
  const getContextMenuItems = useCallback(
    (params: GetContextMenuItemsParams) => {
      const rowData = params.node?.data as WorkbenchRow | undefined;
      const items: any[] = [
        "copy", "paste", "separator",
      ];
      if (rowData) {
        items.push({
          name: "✏️ 添加备注",
          action: () => setNoteEditorRow(rowData),
        });
        items.push({
          name: "🏷️ 标记标签 (" + (rowData.tags?.length || 0) + ")",
          action: () => setTagPickerRow(rowData),
        });
        items.push("separator");
      }
      items.push("export");
      return items;
    },
    []
  );

  // ── Phase 2: note save ────────────────────────────────────
  const handleNoteSave = useCallback(
    async (note: string) => {
      if (!noteEditorRow) return;
      const allRows: WorkbenchRow[] = [];
      gridRef.current?.api.forEachNode((n) => n.data && allRows.push(n.data));
      const updated = allRows.map((r) =>
        r.id === noteEditorRow.id ? { ...r, note } : r
      );
      setRows(updated);
      try {
        await api.syncWorkbench({
          campaign_id: campaignId,
          rows: [{ id: noteEditorRow.id, status: noteEditorRow.status,
            actual_params: noteEditorRow.actual_params ?? {},
            measurements: noteEditorRow.measurements ?? {},
            note } as any],
        });
      } catch {}
      setNoteEditorRow(null);
    },
    [noteEditorRow, campaignId]
  );

  // ── Phase 2: tag save ─────────────────────────────────────
  const handleTagSave = useCallback(
    async (tags: string[]) => {
      if (!tagPickerRow) return;
      const allRows: WorkbenchRow[] = [];
      gridRef.current?.api.forEachNode((n) => n.data && allRows.push(n.data));
      const updated = allRows.map((r) =>
        r.id === tagPickerRow.id ? { ...r, tags } : r
      );
      setRows(updated);
      try {
        await api.syncWorkbench({
          campaign_id: campaignId,
          rows: [{ id: tagPickerRow.id, status: tagPickerRow.status,
            actual_params: tagPickerRow.actual_params ?? {},
            measurements: tagPickerRow.measurements ?? {},
            tags } as any],
        });
      } catch {}
      setTagPickerRow(null);
    },
    [tagPickerRow, campaignId]
  );

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
    setSaveHint(null);
    try {
      const syncMetrics = objectives.map((o) => o.metric);
      const res = await api.syncWorkbench({
        campaign_id: campaignId,
        rows: allRows.map((r) => {
          const measurements: Record<string, number | string> = {};
          for (const m of syncMetrics) {
            const v = r.measurements?.[m];
            if (v !== undefined && v !== null && v !== "") measurements[m] = v as number | string;
          }
          return {
            id: r.id, status: r.status,
            actual_params: r.actual_params ?? {},
            measurements,
            note: r.note,
            tags: r.tags,
          } as any;
        }),
        trigger_loop: autoLoopOnSync ? true : null,
        requirement,
        optimize_engine: optimizeEngine,
        doe_engine: loopDoeEngine,
        campaign_state: campaignState,
      });
      setRows(res.rows);
      const hints: string[] = [];
      if (res.training_message) hints.push(res.training_message);
      if (res.loop_message) hints.push(res.loop_message);
      if (hints.length) setSaveHint(hints.join(" · "));
      onSaved?.(res.rows);
      void refreshWorkbenchStats();
      if (res.loop_task_id) void followLoopTask(res.loop_task_id);
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
    <>
      {/* ── Phase 2 modals ── */}
      {noteEditorRow && (
        <NoteEditor
          initialNote={noteEditorRow.note || ""}
          onSave={handleNoteSave}
          onCancel={() => setNoteEditorRow(null)}
        />
      )}
      {tagPickerRow && (
        <TagPicker
          initialTags={tagPickerRow.tags || []}
          onSave={handleTagSave}
          onCancel={() => setTagPickerRow(null)}
        />
      )}

      <div className="shadow-sm rounded-lg border border-gray-200 dark:border-edge overflow-hidden bg-panel/30">
        {frozen && frozen.length > 0 && (
          <div className="px-2 py-1.5 border-b border-edge/30 bg-ink/30 text-[10px] text-slate-500">
            本 Campaign 指标已冻结（键 = metric）：
            {frozen.map((o: any) => (
              <span key={o.metric} className="ml-2 font-mono text-accent2">
                {o.display_name || o.metric}
              </span>
            ))}
          </div>
        )}
        {error && (
          <p className="text-[11px] text-red-400 px-2 py-1 border-b border-edge/30">{error}</p>
        )}
        {saveHint && !error && (
          <p className="text-[11px] text-emerald-400 px-2 py-1 border-b border-edge/30">{saveHint}</p>
        )}
        <div className="ag-theme-alpine-dark w-full" style={{ height: 320 }}>
          <AgGridReact<WorkbenchRow>
            ref={gridRef}
            theme="legacy"
            rowData={rows}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            enableCellTextSelection={true}
            ensureDomOrder={true}
            processCellFromClipboard={processCellFromClipboard}
            stopEditingWhenCellsLoseFocus={true}
            // ── Phase 1.6: undo / redo ──
            undoRedoCellEditing={true}
            undoRedoCellEditingLimit={20}
            // ── Phase 1.5: sidebar ──
            sideBar={{
              toolPanels: [
                { id: "columns", labelDefault: "列显隐", labelKey: "columns", iconKey: "columns",
                  toolPanel: "agColumnsToolPanel",
                  toolPanelParams: { suppressColumnMove: true, suppressColumnSelectAll: true } },
                { id: "filters", labelDefault: "过滤", labelKey: "filters", iconKey: "filter",
                  toolPanel: "agFiltersToolPanel" },
              ],
              defaultToolPanel: "filters",
              position: "right",
            }}
            // ── Phase 2: right-click menu ──
            getContextMenuItems={getContextMenuItems}
            // ── Phase 3.1: Master / Detail ──
            masterDetail={true}
            detailCellRendererParams={{ objectives }}
            detailRowHeight={200}
            detailCellRenderer={(params: any) => {
              const data = params.data as WorkbenchRow;
              const historyData = (data as any).measurement_history as any[] | undefined;
              return (
                <div className="flex gap-4 p-3 bg-gray-900/50 min-h-[160px]">
                  <div className="flex-1 min-w-0">
                    <h4 className="text-[10px] font-semibold text-slate-400 mb-1">指标趋势</h4>
                    {historyData?.length ? (
                      <div className="text-[10px] text-slate-500">
                        {objectives.map((o) => (
                          <span key={o.metric} className="mr-3">
                            {o.display_name || o.metric}: {JSON.stringify(historyData.slice(-3).map((h: any) => h[o.metric]))}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-[10px] text-slate-600">暂无历史趋势（随闭环迭代自动生成）</p>
                    )}
                  </div>
                  <div className="w-40 text-[10px]">
                    <h4 className="font-semibold text-slate-400 mb-1">标签</h4>
                    <div className="flex flex-wrap gap-0.5">
                      {data.tags?.length ? data.tags.map((t) => (
                        <span key={t} className="text-[9px] px-1.5 py-0.5 rounded bg-accent2/10 text-accent2 border border-accent2/30">{t}</span>
                      )) : <span className="text-slate-600">—</span>}
                    </div>
                    <h4 className="font-semibold text-slate-400 mb-1 mt-2">备注</h4>
                    <p className="text-slate-500 leading-relaxed whitespace-pre-wrap">{data.note || "—"}</p>
                  </div>
                </div>
              );
            }}
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 px-2 py-2 border-t border-edge/40 bg-ink/20">
          <div className="flex flex-col gap-1 min-w-0">
            <span className="text-[10px] text-slate-500">
              {rows.filter((r) => r.status === "Completed").length}/{rows.length} 已完成 ·{" "}
              {objectives.length} 项指标 · 支持 Excel 粘贴
              {loopRoundCount > 0 && (
                <span className={`ml-2 inline-flex items-center px-1.5 py-0.5 rounded border text-[9px] font-medium ${
                  loopConverged
                    ? "border-amber-500/40 text-amber-300 bg-amber-500/10"
                    : "border-violet-500/40 text-violet-300 bg-violet-500/10"
                }`}>
                  闭环 {loopRoundCount} 轮{loopConverged ? " · 已收敛" : ""}
                </span>
              )}
            </span>
            <label className="flex items-center gap-1.5 text-[10px] text-slate-400 cursor-pointer select-none">
              <input type="checkbox" checked={autoLoopOnSync} onChange={(e) => setAutoLoopOnSync(e.target.checked)} className="rounded border-edge" />
              保存后自动分析收敛并建议下一轮
            </label>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button type="button" onClick={handleExportExcel}
              className="text-xs border border-edge rounded px-2 py-1.5 hover:bg-ink/30 text-slate-400">
              导出 Excel
            </button>
            <button type="button" onClick={() => void handleSave()} disabled={saving}
              className="text-xs bg-accent2/90 hover:bg-accent2 text-ink font-semibold rounded px-3 py-1.5 disabled:opacity-40">
              {saving ? "同步中…" : "保存台账并同步"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
