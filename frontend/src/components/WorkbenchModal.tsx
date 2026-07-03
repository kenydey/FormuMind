import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import LabWorkbench from "./LabWorkbench";
import { useStore } from "../store";

export default function WorkbenchModal() {
  const {
    doePlan,
    requirement,
    workbenchCampaignId,
    workbenchStats,
    busy,
    ensureWorkbenchCampaign,
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
      ensureWorkbenchCampaign: s.ensureWorkbenchCampaign,
      refreshWorkbenchStats: s.refreshWorkbenchStats,
      submitResults: s.submitResults,
      setOpenModal: s.setOpenModal,
    }))
  );
  const [ready, setReady] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setInitError(null);
      if (!doePlan) {
        setReady(true);
        return;
      }
      try {
        const id = await ensureWorkbenchCampaign();
        if (!cancelled && id == null && doePlan) {
          setInitError("无法创建实验台账 Campaign");
        }
      } catch (e) {
        if (!cancelled) setInitError(String(e));
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [doePlan, ensureWorkbenchCampaign]);

  if (!ready) {
    return <p className="text-sm text-slate-500 py-6 text-center">加载实验台账…</p>;
  }

  if (!doePlan) {
    return (
      <div className="py-8 text-center space-y-4">
        <p className="text-slate-400 text-sm">
          请先在 <span className="text-accent">DOE 设计</span> 中生成实验方案，系统将自动创建台账 Campaign。
        </p>
        <button
          type="button"
          onClick={() => setOpenModal("doe")}
          className="text-sm border border-accent text-accent rounded px-4 py-2 hover:bg-accent/10"
        >
          打开 DOE 设计
        </button>
      </div>
    );
  }

  if (initError) {
    return <p className="text-sm text-red-400 py-4">{initError}</p>;
  }

  if (workbenchCampaignId == null) {
    return (
      <div className="py-8 text-center space-y-4">
        <p className="text-slate-400 text-sm">台账初始化失败，请重试或重新生成 DOE。</p>
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
    <div className="space-y-3">
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
