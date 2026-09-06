import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import LabWorkbench from "./LabWorkbench";
import ExperimentsBrowser from "./ExperimentsBrowser";
import { useStore } from "../store";
import {
  api,
  formatApiError,
  type ExperimentSearchHit,
  type WorkbenchCampaignSummary,
} from "../api";

export type WorkbenchModalTab = "ledger" | "library";

/**
 * Lab workbench shell: campaign picker + ledger editor, with the former
 * "实验库" folded in as a second tab (cross-campaign search + training corpus).
 */
export default function WorkbenchModal({
  initialTab = "ledger",
}: {
  initialTab?: WorkbenchModalTab;
}) {
  const {
    doePlan,
    requirement,
    workbenchCampaignId,
    workbenchStats,
    busy,
    activeProjectId,
    ensureWorkbenchCampaign,
    selectWorkbenchCampaign,
    refreshWorkbenchStats,
    submitResults,
    setOpenModal,
  } = useStore(
    useShallow((s) => ({
      doePlan: s.doePlan,
      requirement: s.requirement,
      workbenchCampaignId: s.workbenchCampaignId,
      workbenchStats: s.workbenchStats,
      busy: s.busy,
      activeProjectId: s.activeProjectId,
      ensureWorkbenchCampaign: s.ensureWorkbenchCampaign,
      selectWorkbenchCampaign: s.selectWorkbenchCampaign,
      refreshWorkbenchStats: s.refreshWorkbenchStats,
      submitResults: s.submitResults,
      setOpenModal: s.setOpenModal,
    }))
  );
  const [ready, setReady] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<WorkbenchCampaignSummary[]>([]);
  const [campaignsError, setCampaignsError] = useState<string | null>(null);
  const [tab, setTab] = useState<WorkbenchModalTab>(initialTab);
  const [focusRowId, setFocusRowId] = useState<number | null>(null);

  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  async function refreshCampaignList() {
    try {
      const list = await api.listWorkbenchCampaigns();
      const filtered = activeProjectId
        ? list.filter((c) => !c.project_id || c.project_id === activeProjectId)
        : list;
      setCampaigns(filtered);
      setCampaignsError(null);
    } catch (e) {
      setCampaigns([]);
      setCampaignsError(formatApiError(e));
    }
  }

  useEffect(() => {
    void refreshCampaignList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProjectId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setInitError(null);
      // Prefer an existing campaign when no DOE plan is present.
      if (!doePlan) {
        if (!cancelled) setReady(true);
        return;
      }
      try {
        const id = await ensureWorkbenchCampaign();
        if (!cancelled && id == null && doePlan) {
          setInitError("无法创建实验台账 Campaign");
        }
        await refreshCampaignList();
      } catch (e) {
        if (!cancelled) setInitError(String(e));
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doePlan, ensureWorkbenchCampaign]);

  async function openSearchHit(hit: ExperimentSearchHit) {
    setFocusRowId(hit.row_id);
    setTab("ledger");
    if (workbenchCampaignId !== hit.campaign_id) {
      await selectWorkbenchCampaign(hit.campaign_id);
    }
    await refreshCampaignList();
  }

  const tabs = (
    <div
      className="flex gap-1 border-b border-edge/50 pb-2"
      role="tablist"
      aria-label="实验台账视图"
      data-testid="workbench-tabs"
    >
      <button
        type="button"
        role="tab"
        aria-selected={tab === "ledger"}
        data-testid="workbench-tab-ledger"
        onClick={() => setTab("ledger")}
        className={`text-xs rounded px-3 py-1.5 border transition-colors ${
          tab === "ledger"
            ? "border-accent/50 bg-accent/10 text-accent"
            : "border-edge text-slate-400 hover:border-accent/30"
        }`}
      >
        当前台账
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={tab === "library"}
        data-testid="workbench-tab-library"
        onClick={() => setTab("library")}
        className={`text-xs rounded px-3 py-1.5 border transition-colors ${
          tab === "library"
            ? "border-accent/50 bg-accent/10 text-accent"
            : "border-edge text-slate-400 hover:border-accent/30"
        }`}
      >
        检索 / 历史
      </button>
    </div>
  );

  const campaignPicker = (
    <div className="flex flex-wrap items-center gap-2 text-[11px]">
      <label className="text-slate-500 shrink-0">Campaign</label>
      <select
        className="flex-1 min-w-[12rem] bg-ink border border-edge rounded px-2 py-1 text-slate-200"
        value={workbenchCampaignId ?? ""}
        data-testid="workbench-campaign-picker"
        onChange={(e) => {
          const v = e.target.value;
          if (!v) return;
          setFocusRowId(null);
          void selectWorkbenchCampaign(Number(v));
        }}
      >
        <option value="" disabled>
          {campaigns.length ? "选择已有台账…" : "暂无台账"}
        </option>
        {campaigns.map((c) => (
          <option key={c.id} value={c.id}>
            #{c.id} · {c.name} · {c.row_count} 行 · {c.status}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => void refreshCampaignList()}
        className="text-slate-500 hover:text-accent border border-edge rounded px-2 py-1"
      >
        刷新
      </button>
    </div>
  );

  if (!ready) {
    return <p className="text-sm text-slate-500 py-6 text-center">加载实验台账…</p>;
  }

  // Library tab is always available — even before a campaign exists.
  if (tab === "library") {
    return (
      <div className="space-y-3" data-testid="workbench-modal">
        {tabs}
        <ExperimentsBrowser onOpenWorkbenchHit={(hit) => void openSearchHit(hit)} />
      </div>
    );
  }

  if (!doePlan && workbenchCampaignId == null) {
    return (
      <div className="py-2 space-y-4" data-testid="workbench-modal">
        {tabs}
        {campaignPicker}
        {campaignsError && <p className="text-xs text-rose-400">{campaignsError}</p>}
        {campaigns.length > 0 ? (
          <p className="text-slate-400 text-sm text-center">
            选择已有 Campaign 打开台账，或切换到「检索 / 历史」跨批次查找；也可先在 DOE 设计中生成方案。
          </p>
        ) : (
          <div className="text-center space-y-4">
            <p className="text-slate-400 text-sm">
              请先在 <span className="text-accent">DOE 设计</span> 中生成实验方案，系统将自动创建台账
              Campaign；也可先打开「检索 / 历史」查看已有记录。
            </p>
            <div className="flex justify-center gap-2">
              <button
                type="button"
                onClick={() => setOpenModal("doe")}
                className="text-sm border border-accent text-accent rounded px-4 py-2 hover:bg-accent/10"
              >
                打开 DOE 设计
              </button>
              <button
                type="button"
                onClick={() => setTab("library")}
                className="text-sm border border-edge text-slate-300 rounded px-4 py-2 hover:border-accent/40"
              >
                去检索 / 历史
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (initError) {
    return (
      <div className="space-y-3" data-testid="workbench-modal">
        {tabs}
        <p className="text-sm text-red-400 py-4">{initError}</p>
      </div>
    );
  }

  if (workbenchCampaignId == null) {
    return (
      <div className="py-4 text-center space-y-4" data-testid="workbench-modal">
        {tabs}
        {campaignPicker}
        <p className="text-slate-400 text-sm">台账未就绪，请选择已有 Campaign 或重试创建。</p>
        <button
          type="button"
          onClick={() => void ensureWorkbenchCampaign()}
          className="text-sm border border-edge text-slate-300 rounded px-4 py-2 hover:border-accent/50"
        >
          重试创建台账
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="workbench-modal">
      {tabs}
      {campaignPicker}
      {campaignsError && <p className="text-[10px] text-rose-400">{campaignsError}</p>}

      {workbenchStats && (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500 border border-edge/40 rounded-lg px-3 py-2 bg-ink/30">
          <span className="font-mono text-slate-300 truncate max-w-[200px]" title={workbenchStats.name}>
            {workbenchStats.name}
          </span>
          <span className="text-slate-600">·</span>
          <span>{workbenchStats.strategy}</span>
          <span className="ml-auto font-mono text-accent2">
            {workbenchStats.completed}/{workbenchStats.total} 已完成
          </span>
        </div>
      )}

      <LabWorkbench
        campaignId={workbenchCampaignId}
        doePlan={doePlan}
        requirement={requirement}
        onSaved={() => void refreshWorkbenchStats()}
        focusRowId={focusRowId}
      />

      <button
        type="button"
        disabled={busy !== "idle"}
        onClick={() => void submitResults()}
        className="w-full bg-accent2/90 hover:bg-accent2 text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
      >
        {busy === "training" ? "训练中…" : "回灌实验结果并训练模型"}
      </button>
      <p className="text-[10px] text-slate-600 text-center">
        BayBE AI 主动 DOE 将从台账 Completed 行读取 actual_params 与 measurements
      </p>
    </div>
  );
}
