import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, formatApiError } from "../../api";
import { useStore } from "../../store";
import WikiMarkdownReader from "../WikiMarkdownReader";

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
    desc: "汇报用大纲幻灯（后续实现）。",
    dossierSections: "待定",
    muted: true,
  },
];

/** Report generation from DossierPack (P5). */
export default function HubReportsPlaceholderPane() {
  const activeProjectId = useStore(useShallow((s) => s.activeProjectId));
  const [picked, setPicked] = useState<(typeof TEMPLATES)[number] | null>(null);
  const [prompt, setPrompt] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    title: string;
    path: string;
    markdown: string;
    disclaimer?: string;
  } | null>(null);

  const onGenerate = async () => {
    if (!picked || picked.muted) return;
    if (!activeProjectId) {
      setError("请先选择活动项目");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const out = await api.generateWikiReport({
        project_id: activeProjectId,
        template: picked.id,
        prompt,
        use_llm: useLlm,
        ensure_dossier: true,
        persist: true,
      });
      if (!out.ok || !out.markdown || !out.path) {
        throw new Error(out.error || "报告生成失败");
      }
      setResult({
        title: out.title || picked.title,
        path: out.path,
        markdown: out.markdown,
        disclaimer: out.disclaimer,
      });
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 h-full min-h-0 overflow-y-auto" data-testid="hub-reports-pane">
      <p className="text-[11px] text-slate-500">
        文档生成基于<strong className="text-slate-400 font-normal"> 项目卷宗 DossierPack </strong>
        （确定性切片表 + source_ids 溯源）。需开启
        <code className="mx-1">wiki_dossier_report_enabled</code>
        （并依赖 <code>wiki_project_dossier_enabled</code>）。
        不做 Flashcards / Quiz；Mind Map 不进顶级菜单。
      </p>
      <p className="text-[11px] text-slate-500" data-testid="hub-reports-dossier-hint">
        {activeProjectId
          ? `当前活动项目：${activeProjectId} · 生成前会自动 ensure 卷宗（若旗标已开）。`
          : "尚未选择活动项目；生成前需绑定 project_id 卷宗。"}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {TEMPLATES.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => {
              setPicked(t);
              setResult(null);
            }}
            className={`text-left border rounded-lg px-3 py-2.5 transition-colors ${
              t.muted
                ? "border-edge/40 opacity-60 hover:opacity-80"
                : picked?.id === t.id
                  ? "border-accent/60 bg-accent/10"
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

      {picked && !picked.muted && (
        <div className="border border-accent/30 rounded-lg p-3 space-y-2 bg-accent/5">
          <div className="flex justify-between gap-2">
            <h3 className="text-sm text-slate-100">{picked.title}</h3>
            <button type="button" className="text-slate-400 text-xs" onClick={() => setPicked(null)}>
              关闭
            </button>
          </div>
          <p className="text-[11px] text-slate-400">依赖：{picked.dossierSections}</p>
          <label className="block text-[11px] text-slate-400">
            提示词（可选，写入报告备注；不改表内数字）
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="例如：针对水性环氧防腐底漆，输出可行性评估，强调 VOC 与盐雾…"
              className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm text-slate-200"
              data-testid="hub-reports-prompt"
            />
          </label>
          <label className="flex items-center gap-2 text-[11px] text-slate-400">
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
              data-testid="hub-reports-use-llm"
            />
            LLM 执行摘要润色（需 wiki_dossier_llm_narrative；失败则仅确定性稿）
          </label>
          <p className="text-[10px] text-slate-500">
            输出为研发草稿，需人工审核；不构成正式专利/论文代写。对外引用须回链 source_ids /
            测量行，不得把 L2 叙述当 Claim。
          </p>
          {error && (
            <div className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1">
              {error}
            </div>
          )}
          <button
            type="button"
            disabled={busy || !activeProjectId}
            className="px-3 py-1.5 rounded bg-accent text-ink text-sm disabled:opacity-50"
            title="基于卷宗生成"
            data-testid="hub-reports-generate"
            onClick={() => void onGenerate()}
          >
            {busy ? "生成中…" : "基于卷宗生成"}
          </button>
        </div>
      )}

      {result && (
        <div
          className="border border-edge/60 rounded-lg p-3 space-y-2 min-h-0"
          data-testid="hub-reports-result"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-sm text-slate-100">{result.title}</div>
              <code className="text-[10px] text-slate-500">{result.path}</code>
            </div>
            <span className="text-[10px] text-amber-300 border border-amber-500/40 rounded px-1">
              {result.disclaimer || "draft_not_claims"}
            </span>
          </div>
          <div className="max-h-[40vh] overflow-y-auto border border-edge/40 rounded p-2 bg-ink/40">
            <WikiMarkdownReader
              page={{
                path: result.path,
                title: result.title,
                kind: "report",
                flags: ["report", "draft"],
                markdown: result.markdown,
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
