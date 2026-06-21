import Modal from "./Modal";
import RequirementPanel from "./RequirementPanel";
import FormulaLeaderboard from "./FormulaLeaderboard";
import DoeResultsPanel from "./DoeResultsPanel";
import SimPlaceholder from "./SimPlaceholder";
import ProcessOptModal from "./ProcessOptModal";
import LoopModal from "./LoopModal";
import { useStore } from "../store";

type ModalName = "requirements" | "recommend" | "doe" | "optimize" | "process" | "loop";

const ACTIONS: { id: ModalName; icon: string; title: string; desc: string }[] = [
  { id: "requirements", icon: "🧪", title: "技术需求", desc: "设置产品域、基材与优化目标" },
  { id: "recommend", icon: "⭐", title: "推荐配方", desc: "AI 检索并推荐 Top-N 配方" },
  { id: "doe", icon: "🔬", title: "DOE 设计", desc: "生成实验方案并回灌训练" },
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

export default function ActionsPanel() {
  const {
    openModal,
    setOpenModal,
    runResearch,
    runOptimize,
    busy,
    leaderboard,
    models,
    optimizationHistory,
    loopReport,
  } = useStore();

  function badgeFor(id: ModalName) {
    if (id === "recommend") {
      if (busy === "researching") return <Badge tone="amber">检索中…</Badge>;
      if (leaderboard.length > 0) return <Badge tone="accent">{leaderboard.length} 条</Badge>;
    }
    if (id === "doe" && models.length > 0) return <Badge tone="accent">{models.length} 模型</Badge>;
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

  return (
    <aside className="glass rounded-xl p-4 flex flex-col gap-2.5 h-full overflow-y-auto">
      <h2 className="text-sm uppercase tracking-widest text-accent2 shrink-0">操作 · Actions</h2>

      {ACTIONS.map((a) => (
        <button
          key={a.id}
          onClick={() => setOpenModal(a.id)}
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
      >
        <RequirementPanel embedded />
      </Modal>

      <Modal
        title="推荐配方 · Recommended Formulations"
        open={openModal === "recommend"}
        onClose={() => setOpenModal(null)}
        wide
      >
        <button
          disabled={busy !== "idle"}
          onClick={runResearch}
          className="mb-4 w-full bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-2 text-sm disabled:opacity-40"
        >
          {busy === "researching" ? "检索中…" : "检索专利并推荐配方"}
        </button>
        <FormulaLeaderboard />
      </Modal>

      <Modal
        title="DOE 设计 · Design of Experiments"
        open={openModal === "doe"}
        onClose={() => setOpenModal(null)}
        wide
      >
        <DoeResultsPanel />
      </Modal>

      <Modal
        title="寻优收敛 · Optimization"
        open={openModal === "optimize"}
        onClose={() => setOpenModal(null)}
        wide
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
        wide
      >
        <ProcessOptModal />
      </Modal>

      <Modal
        title="🔄 自驱动研发闭环 · Self-Driving Loop"
        open={openModal === "loop"}
        onClose={() => setOpenModal(null)}
        wide
      >
        <LoopModal />
      </Modal>
    </aside>
  );
}
