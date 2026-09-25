import { useMemo, useState } from "react";
import { useStore } from "../store";

type PathId = "formula" | "substitute" | "knowledge";

const PATHS: {
  id: PathId;
  icon: string;
  title: string;
  desc: string;
  steps: string[];
  maturity: "stable" | "beta";
}[] = [
  {
    id: "formula",
    icon: "🧪",
    title: "新配方开发",
    desc: "需求 → 研究/推荐 → DOE → 台账闭环",
    steps: ["技术需求", "推荐配方", "DOE 设计", "实验台账", "自驱动闭环"],
    maturity: "stable",
  },
  {
    id: "substitute",
    icon: "🔁",
    title: "材料替代",
    desc: "缺货/涨价 → 候选 → 验证实验",
    steps: ["材料库", "推荐/替代", "实验台账"],
    maturity: "stable",
  },
  {
    id: "knowledge",
    icon: "📚",
    title: "项目知识沉淀",
    desc: "资料入库 → Wiki/图谱 → 可追溯问答",
    steps: ["知识库·资料", "Wiki", "质量运营", "卷宗报告"],
    maturity: "beta",
  },
];

/**
 * Batch E: light path wizard — deep-links existing modals; no new engines.
 */
export default function PathWizard() {
  const [active, setActive] = useState<PathId | null>(null);
  const setOpenModal = useStore((s) => s.setOpenModal);
  const openKnowledgeHub = useStore((s) => s.openKnowledgeHub);
  const refreshWorkbenchStats = useStore((s) => s.refreshWorkbenchStats);

  const current = useMemo(() => PATHS.find((p) => p.id === active) || null, [active]);

  function runStep(path: PathId, step: string) {
    if (path === "formula") {
      if (step.includes("需求")) setOpenModal("requirements");
      else if (step.includes("推荐")) setOpenModal("recommend");
      else if (step.includes("DOE")) setOpenModal("doe");
      else if (step.includes("台账")) {
        setOpenModal("workbench");
        void refreshWorkbenchStats();
      } else if (step.includes("闭环")) setOpenModal("loop");
    } else if (path === "substitute") {
      if (step.includes("材料")) setOpenModal("materials");
      else if (step.includes("推荐") || step.includes("替代")) setOpenModal("recommend");
      else if (step.includes("台账")) {
        setOpenModal("workbench");
        void refreshWorkbenchStats();
      }
    } else if (path === "knowledge") {
      if (step.includes("资料")) openKnowledgeHub("materials");
      else if (step.includes("Wiki")) openKnowledgeHub("wiki");
      else if (step.includes("质量")) openKnowledgeHub("quality");
      else if (step.includes("卷宗") || step.includes("报告")) openKnowledgeHub("reports");
    }
  }

  return (
    <div className="space-y-2" data-testid="path-wizard">
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] uppercase tracking-widest text-slate-500">主路径 · Paths</h3>
        {active && (
          <button
            type="button"
            className="text-[10px] text-slate-500 hover:text-slate-300"
            onClick={() => setActive(null)}
            data-testid="path-wizard-clear"
          >
            收起
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 gap-1.5">
        {PATHS.map((p) => {
          const on = active === p.id;
          return (
            <button
              key={p.id}
              type="button"
              data-testid={`path-card-${p.id}`}
              onClick={() => setActive(on ? null : p.id)}
              className={`text-left rounded-lg border px-2.5 py-2 transition-colors ${
                on ? "border-accent/60 bg-accent/10" : "border-edge hover:border-accent/40"
              }`}
            >
              <div className="flex items-center gap-2">
                <span>{p.icon}</span>
                <span className="text-xs text-slate-100 font-medium">{p.title}</span>
                <span
                  className={`ml-auto text-[9px] px-1 rounded border ${
                    p.maturity === "stable"
                      ? "border-emerald-500/30 text-emerald-400"
                      : "border-amber-500/30 text-amber-300"
                  }`}
                >
                  {p.maturity}
                </span>
              </div>
              <p className="text-[10px] text-slate-500 mt-0.5">{p.desc}</p>
            </button>
          );
        })}
      </div>
      {current && (
        <ol
          className="rounded-lg border border-edge/60 bg-ink/30 p-2 space-y-1"
          data-testid={`path-steps-${current.id}`}
        >
          {current.steps.map((step, i) => (
            <li key={step}>
              <button
                type="button"
                data-testid={`path-step-${current.id}-${i}`}
                onClick={() => runStep(current.id, step)}
                className="w-full text-left text-[11px] px-2 py-1.5 rounded border border-transparent hover:border-accent/40 hover:bg-accent/5 text-slate-300"
              >
                <span className="text-slate-500 mr-2">{i + 1}.</span>
                {step}
                <span className="float-right text-[9px] text-accent2">打开</span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
