import Modal from "../Modal";
import { useStore } from "../../store";
import { useShallow } from "zustand/react/shallow";
import type { KnowledgeHubTab } from "./types";
import HubMaterialsPane from "./HubMaterialsPane";
import HubWikiPane from "./HubWikiPane";
import HubGraphPane from "./HubGraphPane";
import HubReportsPlaceholderPane from "./HubReportsPlaceholderPane";

const TABS: { id: KnowledgeHubTab; label: string; hint: string }[] = [
  { id: "materials", label: "资料", hint: "当前项目入库文档" },
  { id: "wiki", label: "Wiki", hint: "当前项目编译页 / 卷宗" },
  { id: "graph", label: "图谱", hint: "全局探针（未项目切分）" },
  { id: "reports", label: "文档生成", hint: "当前项目卷宗报告" },
];

/** Knowledge Hub shell — right-rail materials governance entry (H0). */
export default function KnowledgeHubModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { tab, setTab } = useStore(
    useShallow((s) => ({
      tab: s.knowledgeHubTab,
      setTab: s.setKnowledgeHubTab,
    })),
  );

  return (
    <Modal
      title="📚 知识库 · Knowledge Hub（按当前项目）"
      open={open}
      onClose={onClose}
      size="xl"
      testId="modal-knowledge-hub"
    >
      <div className="flex flex-col gap-3 h-[min(70vh,720px)]">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 shrink-0" data-testid="hub-tab-cards">
          {TABS.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                data-testid={`hub-tab-${t.id}`}
                onClick={() => setTab(t.id)}
                className={`text-left rounded-lg border px-3 py-2 transition-colors ${
                  active
                    ? "border-accent/60 bg-accent/10"
                    : "border-edge hover:border-accent/40"
                }`}
              >
                <div className="text-sm font-medium text-slate-100 flex items-center gap-1">
                  {t.label}
                  {t.id === "reports" && (
                    <span className="text-[9px] text-emerald-400/90 border border-emerald-500/30 rounded px-1">
                      卷宗
                    </span>
                  )}
                </div>
                <p className="text-[10px] text-slate-500 mt-0.5">{t.hint}</p>
              </button>
            );
          })}
        </div>
        <div className="flex-1 min-h-0">
          {tab === "materials" && <HubMaterialsPane open={open} />}
          {tab === "wiki" && <HubWikiPane active={open && tab === "wiki"} />}
          {tab === "graph" && <HubGraphPane active={open && tab === "graph"} />}
          {tab === "reports" && <HubReportsPlaceholderPane />}
        </div>
      </div>
    </Modal>
  );
}
