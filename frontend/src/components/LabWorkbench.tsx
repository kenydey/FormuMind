import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type { ColDef, ICellRendererParams, GetContextMenuItemsParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

ModuleRegistry.registerModules([AllCommunityModule]);

import { api } from "../api";
import type { DOEPlan, Requirement, WorkbenchRow, WorkbenchQuality } from "../api";
import BiasTrendPanel from "./BiasTrendPanel";
import { useStore } from "../store";
import {
  buildWorkbenchColumnDefs,
  effectiveObjectives,
  factorKeysFromPlan,
} from "../utils/workbenchColumns";
import NoteEditor from "./NoteEditor";
import TagPicker from "./TagPicker";
import AttachmentPreview from "./AttachmentPreview";
import LineageTree from "./LineageTree";
import ExperimentDiff from "./ExperimentDiff";
import ExperimentDetail from "./ExperimentDetail";
import QCReportModal from "./QCReportModal";
import CampaignRoundsModal from "./CampaignRoundsModal";

// F22：稳定模块级组件，避免每次渲染生成新的内联箭头函数导致 AG Grid
// 反复重建 detail 面板。objectives 由 detailCellRendererParams 注入。
function ExperimentDetailRenderer(params: any) {
  return <ExperimentDetail data={params.data} objectives={params.objectives} />;
}

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
  const [biasSummary, setBiasSummary] = useState<NonNullable<import("../api").WorkbenchSyncResponse["prediction_bias"]> | null>(null);
  const [quality, setQuality] = useState<WorkbenchQuality | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [loopRoundCount, setLoopRoundCount] = useState(0);
  const [loopConverged, setLoopConverged] = useState(false);
  const [apiSnapshot, setApiSnapshot] = useState<ReturnType<typeof effectiveObjectives> | undefined>();

  // ── Phase 2 modal state ──────────────────────────────────────
  const [noteEditorRow, setNoteEditorRow] = useState<WorkbenchRow | null>(null);
  const [tagPickerRow, setTagPickerRow] = useState<WorkbenchRow | null>(null);
  const [attachmentRow, setAttachmentRow] = useState<WorkbenchRow | null>(null);
  const [attachmentCounts, setAttachmentCounts] = useState<Record<number, number>>({});
  const [lineageRow, setLineageRow] = useState<WorkbenchRow | null>(null);
  const [diffPair, setDiffPair] = useState<[WorkbenchRow, WorkbenchRow] | null>(null);
  const [qcReportRow, setQcReportRow] = useState<WorkbenchRow | null>(null);
  const [showRounds, setShowRounds] = useState(false);

  const workbenchObjectivesSnapshot = useStore((s) => s.workbenchObjectivesSnapshot);
  const autoLoopOnSync = useStore((s) => s.autoLoopOnSync);
  const setAutoLoopOnSync = useStore((s) => s.setAutoLoopOnSync);
  const optimizeEngine = useStore((s) => s.optimizeEngine);
  const loopDoeEngine = useStore((s) => s.loopDoeEngine);
  const campaignState = useStore((s) => s.campaignState);
  const followLoopTask = useStore((s) => s.followLoopTask);
  const refreshWorkbenchStats = useStore((s) => s.refreshWorkbenchStats);
  const recomputePredicted = useStore((s) => s.recomputePredicted);
  const workbenchCampaignId = useStore((s) => s.workbenchCampaignId);

  const objectives = useMemo(
    () => effectiveObjectives(
      requirement.objectives ?? [],
      workbenchObjectivesSnapshot ?? apiSnapshot
    ),
    [requirement.objectives, workbenchObjectivesSnapshot, apiSnapshot]
  );

  const factorKeys = useMemo(() => factorKeysFromPlan(doePlan, rows), [doePlan, rows]);

  // F19/F20：切换 campaignId 时立即清空上一路的状态，避免旧 campaign 的
  // attachmentCounts / apiSnapshot / loop 统计残留，并配合上方加载 effect 的
  // cancelled 标志防止慢请求回写错乱。
  useEffect(() => {
    setRows([]);
    setError(null);
    setSaveHint(null);
    setLoopRoundCount(0);
    setLoopConverged(false);
    setApiSnapshot(undefined);
    setAttachmentCounts({});
    setQuality(null);
  }, [campaignId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const q = await api.getWorkbenchQuality(campaignId);
        if (!cancelled) setQuality(q);
      } catch {
        if (!cancelled) setQuality(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  const reconcileRefs = useCallback(async () => {
    setReconciling(true);
    try {
      await api.reconcileWorkbench(campaignId);
      const q = await api.getWorkbenchQuality(campaignId);
      setQuality(q);
      setSaveHint("已清理失效引用");
    } catch (e) {
      setError(String(e));
    } finally {
      setReconciling(false);
    }
  }, [campaignId]);

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
          // Phase 2.1: preload attachment counts for the 📎 badge column.
          void (async () => {
            const counts: Record<number, number> = {};
            await Promise.all(
              data.rows.map(async (r) => {
                try {
                  counts[r.id] = (
                    await api.getWorkbenchAttachments(campaignId, r.id)
                  ).length;
                } catch {
                  counts[r.id] = 0;
                }
              })
            );
            if (!cancelled) setAttachmentCounts(counts);
          })();
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
    const cols = buildWorkbenchColumnDefs(factorKeys, objectives, doePlan, attachmentCounts);
    const statusCol = cols.find(
      (c): c is ColDef<WorkbenchRow> => "field" in c && c.field === "status"
    );
    if (statusCol) statusCol.cellRenderer = StatusCellRenderer;
    return cols as ColDef<WorkbenchRow>[];
  }, [factorKeys, objectives, doePlan, attachmentCounts]);

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

  // ── Phase 1.4: CSV export ────────────────────────────────────
  // ag-grid Community 版无 exportDataAsExcel（Enterprise 功能，静默无效），改用 CSV。
  const handleExportExcel = useCallback(() => {
    const agApi = gridRef.current?.api;
    if (!agApi) return;
    agApi.exportDataAsCsv({
      fileName: `DOE-C${campaignId}-${new Date().toISOString().slice(0, 10)}.csv`,
      processCellCallback: (p) => p.value ?? "",
    });
  }, [campaignId]);

  // ── Phase 3.3: side-by-side diff of two selected rows ──────────
  const handleCompare = useCallback(() => {
    const selected = gridRef.current?.api.getSelectedRows() ?? [];
    if (selected.length === 2) {
      setError(null);
      setDiffPair([selected[0], selected[1]]);
    } else {
      setError("请选中恰好 2 行进行对比");
    }
  }, []);

  // ── Phase 2: context menu ────────────────────────────────────
  const getContextMenuItems = useCallback(
    (params: GetContextMenuItemsParams) => {
      const rowData = params.node?.data as WorkbenchRow | undefined;
      const items: any[] = [
        "copy", "paste", "separator",
      ];
      if (rowData) {
        items.push({
          name: "📄 上传检测报告",
          action: () => setQcReportRow(rowData),
        });
        items.push({
          name: "📎 查看/上传附件 (" + (attachmentCounts[rowData.id] || 0) + ")",
          action: () => setAttachmentRow(rowData),
        });
        items.push({
          name: "🧬 查看谱系",
          action: () => setLineageRow(rowData),
        });
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
    [attachmentCounts]
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
      } catch (e) {
        // 不再静默吞错（F21）：提示用户并保留编辑器，便于重试
        setError(`笔记保存失败：${e instanceof Error ? e.message : String(e)}`);
        return;
      }
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
      } catch (e) {
        // 不再静默吞错（F21）：提示并回滚本地乐观更新
        setError(`标签保存失败：${e instanceof Error ? e.message : String(e)}`);
        setRows((prev) => prev.map((r) => r.id === tagPickerRow.id ? tagPickerRow : r));
        setTagPickerRow(null);
        return;
      }
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
            // 过滤 null/非有限值：清空单元格会产生 null，后端严格校验会 422 整体失败
            actual_params: Object.fromEntries(
              Object.entries(r.actual_params ?? {}).filter(
                ([, v]) => typeof v === "number" && Number.isFinite(v)
              )
            ),
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
      if (res.kg_written) hints.push(`KG 回流 ${res.kg_written} 条实测证据`);
      if (hints.length) setSaveHint(hints.join(" · "));
      if (res.prediction_bias?.by_metric && Object.keys(res.prediction_bias.by_metric).length) {
        setBiasSummary(res.prediction_bias);
      } else {
        setBiasSummary(null);
      }
      onSaved?.(res.rows);
      void refreshWorkbenchStats();
      void recomputePredicted();
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
      {attachmentRow && (
        <AttachmentPreview
          campaignId={campaignId}
          rowId={attachmentRow.id}
          onClose={() => setAttachmentRow(null)}
          onChanged={(count) =>
            setAttachmentCounts((prev) => ({ ...prev, [attachmentRow.id]: count }))
          }
        />
      )}
      {lineageRow && (
        <LineageTree
          campaignId={campaignId}
          rowId={lineageRow.id}
          onClose={() => setLineageRow(null)}
        />
      )}
      {diffPair && (
        <ExperimentDiff
          a={diffPair[0]}
          b={diffPair[1]}
          onClose={() => setDiffPair(null)}
        />
      )}
      {qcReportRow && (
        <QCReportModal
          campaignId={campaignId}
          rowId={qcReportRow.id}
          onClose={() => setQcReportRow(null)}
        />
      )}
      {showRounds && (
        <CampaignRoundsModal
          campaignId={campaignId}
          onClose={() => setShowRounds(false)}
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
        {biasSummary && !error && (
          <div className="px-2 py-1.5 border-b border-edge/30 bg-amber-500/5 text-[10px]">
            <div className="flex items-center justify-between">
              <span className="text-amber-300 font-medium">预测偏差校准（{biasSummary.n_rows} 行，预测−实测）</span>
              <button type="button" onClick={() => setBiasSummary(null)} className="text-slate-500 hover:text-slate-300">×</button>
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {Object.entries(biasSummary.by_metric).map(([metric, s]) => (
                <span key={metric} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-amber-500/20 bg-amber-500/10 font-mono text-[10px] text-amber-200">
                  <span className="font-semibold">{metric}</span>
                  <span>mean {s.mean_error > 0 ? `+${s.mean_error}` : s.mean_error}</span>
                  <span className="text-slate-400">rmse {s.rmse}</span>
                  <span className="text-slate-400">mae {s.mae}</span>
                  <span className="text-slate-500">n={s.n}</span>
                </span>
              ))}
            </div>
          </div>
        )}
        {quality && (quality.stale_count > 0 || quality.dropped_total > 0) && (
          <div className="px-2 py-1.5 border-b border-edge/30 bg-rose-500/5 text-[10px]">
            <div className="flex items-center justify-between">
              <span className="text-rose-300 font-medium">数据质量</span>
              <button
                type="button"
                onClick={() => void reconcileRefs()}
                disabled={reconciling}
                className="text-slate-400 hover:text-slate-200 disabled:opacity-40"
              >
                {reconciling ? "清理中…" : quality.stale_count > 0 ? "清理失效引用" : ""}
              </button>
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {quality.stale_count > 0 && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-rose-500/30 bg-rose-500/10 font-mono text-rose-300">
                  失效引用 {quality.stale_count}
                </span>
              )}
              {quality.errors_count > 0 && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 font-mono text-amber-300">
                  探测失败 {quality.errors_count}
                </span>
              )}
              {quality.dropped_total > 0 && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-amber-500/20 bg-amber-500/10 font-mono text-amber-200">
                  累计丢弃非法值 {quality.dropped_total}
                </span>
              )}
            </div>
          </div>
        )}
        <BiasTrendPanel campaignId={(campaignId ?? workbenchCampaignId) ?? null} />
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
            // ── Phase 3.3: multi-select for diff ──
            rowSelection="multiple"
            // ── Phase 3.1: Master / Detail ──
            masterDetail={true}
            detailCellRendererParams={{ objectives }}
            detailRowHeight={200}
            detailCellRenderer={ExperimentDetailRenderer}
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
            <button type="button" onClick={() => setShowRounds(true)}
              className="text-xs border border-edge rounded px-2 py-1.5 hover:bg-ink/30 text-slate-400">
              轮次历史
            </button>
            <button type="button" onClick={handleCompare}
              className="text-xs border border-edge rounded px-2 py-1.5 hover:bg-ink/30 text-slate-400">
              对比选中
            </button>
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
