import Modal from "./Modal";
import RequirementPanel from "./RequirementPanel";
import FormulaLeaderboard from "./FormulaLeaderboard";
import DoeResultsPanel from "./DoeResultsPanel";
import WorkbenchModal from "./WorkbenchModal";
import SimPlaceholder from "./SimPlaceholder";
import ProcessOptModal from "./ProcessOptModal";
import LoopModal from "./LoopModal";
import { useStore } from "../store";
import { useShallow } from "zustand/react/shallow";

type ModalName = "requirements" | "recommend" | "doe" | "workbench" | "optimize" | "process" | "loop";

const ACTIONS: { id: ModalName; icon: string; title: string; desc: string }[] = [
  { id: "requirements", icon: "🧪", title: "技术需求", desc: "设置产品域、基材与优化目标" },
  { id: "recommend", icon: "⭐", title: "推荐配方", desc: "AI 检索并推荐 Top-N 配方" },
  { id: "doe", icon: "🔬", title: "DOE 设计", desc: "生成实验方案并导出记录表" },
  { id: "workbench", icon: "📋", title: "实验台账", desc: "填报实际参数与实测值，同步至 BayBE 闭环" },
  { id: "optimize", icon: "📈", title: "寻优收敛", desc: "贝叶斯多目标闭环优化" },
  { id: "process", icon: "⚙️", title: "工艺优化", desc: "固化/分散/膜厚等工艺参数优化" },
  { id: "loop", icon: "🔄", title: "自驱动闭环", desc: "数据→重训→寻优→下一批 DOE 一键迭代" },
];

function Badge({ children, tone }: { children: React.ReactNode; tone: "accent" | "amber" }) {
  const cls =
    tone === "accent"
      ? "bg-accent/20 text-accent border-accent/40"
      : "bg-amber-500/20 text-amber-400 border-amber-500/40";
  return (
    <span className={`text-[10px] font-mono rounded-full border px-1.5 py-0.5 ${cls}`}>
      {children}
    </span>
  );
}

const RECOMMEND_STAGES = [
  { id: "retrieve", label: "检索" },
  { id: "grade", label: "评估" },
  { id: "recommend", label: "推荐" },
] as const;

function recommendStageIndex(stage: string): number {
  const idx = RECOMMEND_STAGES.findIndex((s) => s.id === stage);
  return idx >= 0 ? idx : 0;
}

export default function ActionsPanel() {
  const {
    openModal,
    setOpenModal,
    runResearch,
    runOptimize,
    busy,
    formulationBusy,
    recommendStage,
    recommendMessage,
    sources,
    task,
    leaderboard,
    models,
    optimizationHistory,
    loopReport,
    workbenchStats,
    refreshWorkbenchStats,
  } = useStore(
    useShallow((s) => ({
      openModal: s.openModal,
      setOpenModal: s.setOpenModal,
      runResearch: s.runResearch,
      runOptimize: s.runOptimize,
      busy: s.busy,
      formulationBusy: s.formulationBusy,
      recommendStage: s.recommendStage,
      recommendMessage: s.recommendMessage,
      sources: s.sources,
      task: s.task,
      leaderboard: s.leaderboard,
      models: s.models,
      optimizationHistory: s.optimizationHistory,
      loopReport: s.loopReport,
      workbenchStats: s.workbenchStats,
      refreshWorkbenchStats: s.refreshWorkbenchStats,
    }))
  );

  const activeRecommendIdx = recommendStageIndex(recommendStage);
  const recommendProgressPct = formulationBusy
    ? Math.round(((task?.progress ?? 0) || (activeRecommendIdx + 1) / RECOMMEND_STAGES.length) * 100)
    : 0;

  function badgeFor(id: ModalName) {
    if (id === "recommend") {
      if (formulationBusy) return <Badge tone="amber">配方检索中…</Badge>;
      if (leaderboard.length > 0) return <Badge tone="accent">{leaderboard.length} 条</Badge>;
    }
    if (id === "doe" && models.length > 0) return <Badge tone="accent">{models.length} 模型</Badge>;
    if (id === "workbench" && workbenchStats && workbenchStats.total > 0) {
      return (
        <Badge tone={workbenchStats.completed > 0 ? "accent" : "amber"}>
          {workbenchStats.completed}/{workbenchStats.total}
        </Badge>
      );
    }
    if (id === "optimize") {
      if (busy === "optimizing") return <Badge tone="amber">寻优中…</Badge>;
      if (optimizationHistory.length > 0) return <Badge tone="accent">已收敛</Badge>;
    }
    if (id === "loop") {
      if (busy === "looping") return <Badge tone="amber">迭代中…</Badge>;
      if (loopReport) return <Badge tone="accent">已迭代</Badge>;
    }
    return null;
  }

  function openWorkbench() {
    setOpenModal("workbench");
    void refreshWorkbenchStats();
  }

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-2.5 h-full overflow-y-auto">
      <h2 className="text-sm uppercase tracking-widest text-accent2 shrink-0">操作 · Actions</h2>

      {ACTIONS.map((a) => (
        <button
          key={a.id}
          onClick={() => (a.id === "workbench" ? openWorkbench() : setOpenModal(a.id))}
          className="text-left border border-edge rounded-lg px-3 py-2.5 hover:border-accent/50 hover:bg-accent/5 transition-colors group"
        >
          <div className="flex items-center gap-2">
            <span className="text-lg">{a.icon}</span>
            <span className="text-sm font-semibold text-slate-200 group-hover:text-accent">
              {a.title}
            </span>
            <span className="ml-auto">{badgeFor(a.id)}</span>
          </div>
          <p className="text-[11px] text-slate-500 mt-1 ml-7">{a.desc}</p>
        </button>
      ))}

      {/* ── Modals ── */}
      <Modal
        title="技术需求 · Requirements"
        open={openModal === "requirements"}
        onClose={() => setOpenModal(null)}
        size="md"
      >
        <RequirementPanel embedded />
      </Modal>

      <Modal
        title="推荐配方 · Recommended Formulations"
        open={openModal === "recommend"}
        onClose={() => setOpenModal(null)}
        size="xl"
      >
        <div className="mb-4 space-y-3">
          <p className="text-[11px] text-slate-500">
            从 ColBERT 知识库经 CRAG 评估后推荐配方（源策略由后端配置，无需勾选专利/文献）。
          </p>
          {sources.length === 0 && (
            <p className="text-[11px] text-amber-400/90 border border-amber-500/30 bg-amber-500/5 rounded px-2.5 py-2">
              建议先在左栏检索或上传资料以充实知识库；离线种子语料仍可用于基础推荐。
            </p>
          )}
          {formulationBusy && (
            <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5">
              <div className="flex items-center justify-between text-[11px] text-slate-400 mb-2">
                <span className="text-accent2 uppercase tracking-widest">推荐配方 · CRAG</span>
                <span>{recommendMessage || "处理中…"}</span>
              </div>
              <div className="flex gap-1 mb-2">
                {RECOMMEND_STAGES.map((s, i) => {
                  const done = i < activeRecommendIdx;
                  const active = i === activeRecommendIdx;
                  return (
                    <div key={s.id} className="flex-1 min-w-0">
                      <div
                        className={`h-1 rounded-full transition-colors ${
                          done
                            ? "bg-accent"
                            : active
                              ? "bg-accent/70 animate-pulse"
                              : "bg-edge"
                        }`}
                      />
                      <div
                        className={`mt-1 text-[9px] text-center truncate ${
                          active ? "text-accent font-semibold" : done ? "text-slate-400" : "text-slate-600"
                        }`}
                      >
                        {s.label}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="h-1 bg-edge rounded overflow-hidden">
                <div
                  className="h-full bg-accent/80 transition-all duration-500"
                  style={{ width: `${Math.min(100, recommendProgressPct)}%` }}
                />
              </div>
            </div>
          )}
          <button
            disabled={busy !== "idle" || formulationBusy}
            onClick={runResearch}
            className="w-full bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
          >
            {formulationBusy ? "检索中…" : "从知识库推荐配方"}
          </button>
        </div>
        <FormulaLeaderboard />
      </Modal>

      <Modal
        title="DOE 设计 · Design of Experiments"
        open={openModal === "doe"}
        onClose={() => setOpenModal(null)}
        size="lg"
      >
        <DoeResultsPanel />
      </Modal>

      <Modal
        title="实验台账 · Lab Workbench"
        open={openModal === "workbench"}
        onClose={() => setOpenModal(null)}
        size="xl"
      >
        <WorkbenchModal />
      </Modal>

      <Modal
        title="寻优收敛 · Optimization"
        open={openModal === "optimize"}
        onClose={() => setOpenModal(null)}
        size="lg"
      >
        <button
          disabled={busy !== "idle"}
          onClick={runOptimize}
          className="mb-4 w-full border border-accent2 text-accent2 hover:bg-accent2/10 rounded px-3 py-2 text-sm disabled:opacity-40"
        >
          {busy === "optimizing" ? "寻优中…" : "运行 DOE 寻优闭环"}
        </button>
        {optimizationHistory.length > 0 ? (
          <div className="h-80 [&>div]:h-full">
            <SimPlaceholder />
          </div>
        ) : (
          <p className="text-slate-500 text-sm">运行寻优后，此处显示收敛折线图与最优配方。</p>
        )}
      </Modal>

      <Modal
        title="⚙️ 工艺参数优化 · Process Optimization"
        open={openModal === "process"}
        onClose={() => setOpenModal(null)}
        size="lg"
      >
        <ProcessOptModal />
      </Modal>

      <Modal
        title="🔄 自驱动研发闭环 · Self-Driving Loop"
        open={openModal === "loop"}
        onClose={() => setOpenModal(null)}
        size="lg"
      >
        <LoopModal />
      </Modal>
    </aside>
  );
}
