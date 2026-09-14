import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../../store";

const TEMPLATES: {
  id: string;
  title: string;
  desc: string;
  dossierSections: string;
  muted?: boolean;
}[] = [
  {
    id: "briefing",
    title: "文献简报 · Briefing Doc",
    desc: "汇总已选知识库来源的关键洞察与引用摘录。",
    dossierSections: "主读卷宗 S1 + S6 + S8",
  },
  {
    id: "feasibility",
    title: "技术可行性评估",
    desc: "材料/工艺替代方案的可行性、风险与试验建议。",
    dossierSections: "主读卷宗 S1 + S3 + S4 + S5",
  },
  {
    id: "formula-compare",
    title: "配方对比纪要",
    desc: "对比候选配方组分、性能与文献依据。",
    dossierSections: "主读卷宗 S3 + S6（+ formulation_versions）",
  },
  {
    id: "patent-memo",
    title: "专利挖掘备忘",
    desc: "公开号、权利要求要点与规避/借鉴清单（草稿）。",
    dossierSections: "主读卷宗 S2（source_ids → Raw 回链）",
  },
  {
    id: "deck",
    title: "演示文稿 · Slide Deck",
    desc: "汇报用大纲幻灯（Report 之后实现）。",
    dossierSections: "待定",
    muted: true,
  },
];

/** Report generation placeholder (H4 / P4.5) — pack-backed, no generate API yet. */
export default function HubReportsPlaceholderPane() {
  const activeProjectId = useStore(useShallow((s) => s.activeProjectId));
  const [picked, setPicked] = useState<(typeof TEMPLATES)[number] | null>(null);
  const [prompt, setPrompt] = useState("");

  return (
    <div className="flex flex-col gap-3 h-full min-h-0 overflow-y-auto" data-testid="hub-reports-pane">
      <p className="text-[11px] text-slate-500">
        文档生成将基于<strong className="text-slate-400 font-normal"> 项目卷宗 DossierPack </strong>
        （GET /api/wiki/dossier/&#123;project_id&#125;/pack）与知识库来源产出研发草稿。
        <strong className="text-slate-400 font-normal"> 当前为预留界面，不调用生成 API。</strong>
        不做 Flashcards / Quiz；Mind Map 不进顶级菜单。
      </p>
      <p className="text-[11px] text-slate-500" data-testid="hub-reports-dossier-hint">
        {activeProjectId
          ? `当前活动项目：${activeProjectId} · 请先在 Wiki 页生成/刷新「项目卷宗」。`
          : "尚未选择活动项目；生成前需绑定 project_id 卷宗。"}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {TEMPLATES.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setPicked(t)}
            className={`text-left border rounded-lg px-3 py-2.5 transition-colors ${
              t.muted
                ? "border-edge/40 opacity-60 hover:opacity-80"
                : "border-edge hover:border-accent/50 hover:bg-accent/5"
            }`}
          >
            <div className="text-sm text-slate-200">{t.title}</div>
            <p className="text-[11px] text-slate-500 mt-1">{t.desc}</p>
            <p className="text-[10px] text-accent/80 mt-1">{t.dossierSections}</p>
            {t.muted && (
              <span className="text-[10px] text-amber-400/80 mt-1 inline-block">子项 · 稍后</span>
            )}
          </button>
        ))}
      </div>

      {picked && (
        <div className="border border-accent/30 rounded-lg p-3 space-y-2 bg-accent/5">
          <div className="flex justify-between gap-2">
            <h3 className="text-sm text-slate-100">{picked.title}</h3>
            <button type="button" className="text-slate-400 text-xs" onClick={() => setPicked(null)}>
              关闭
            </button>
          </div>
          <p className="text-[11px] text-slate-400">依赖：{picked.dossierSections}</p>
          <label className="block text-[11px] text-slate-400">
            提示词（预留）
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="例如：针对水性环氧防腐底漆，输出可行性评估，强调 VOC 与盐雾…"
              className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm text-slate-200"
            />
          </label>
          <p className="text-[10px] text-slate-500">
            输出为研发草稿，需人工审核；不构成正式专利/论文代写。对外引用须回链 source_ids / 测量行，不得把 L2 叙述当 Claim。
          </p>
          <button
            type="button"
            disabled
            className="px-3 py-1.5 rounded bg-accent/30 text-ink text-sm opacity-60 cursor-not-allowed"
            title="基于卷宗生成（即将推出）"
            data-testid="hub-reports-generate"
          >
            基于卷宗生成（即将推出）
          </button>
        </div>
      )}
    </div>
  );
}
