import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api, formatApiError } from "../../api";
import { useStore } from "../../store";
import { saveTextToProjectShelf, shelfFilename } from "../../utils/export";
import WikiMarkdownReader from "../WikiMarkdownReader";

/** Grayscale keys required for Hub dossier → Report generate/export. */
const REPORT_FLAG_ATTRS = [
  "wiki_enabled",
  "wiki_project_dossier_enabled",
  "wiki_dossier_report_enabled",
] as const;

type ReportFlagAttr = (typeof REPORT_FLAG_ATTRS)[number];

const REPORT_FLAG_LABEL: Record<ReportFlagAttr, string> = {
  wiki_enabled: "Wiki",
  wiki_project_dossier_enabled: "卷宗",
  wiki_dossier_report_enabled: "Report",
};

function isFlagGateError(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("wiki_dossier_report_enabled") ||
    m.includes("wiki_project_dossier_enabled") ||
    m.includes("wiki_enabled") ||
    (m.includes("enabled") && m.includes("false"))
  );
}

const TEMPLATES: {
  id: string;
  title: string;
  desc: string;
  dossierSections: string;
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
    dossierSections: "主读卷宗 S3 + S6",
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
    desc: "汇报大纲幻灯；可导出 PPTX。",
    dossierSections: "S1/S3/S4/S6/S2 大纲分页",
  },
];

type ExportCaps = {
  md?: boolean;
  docx?: boolean;
  pdf?: boolean;
  pptx?: boolean;
  cjk_font?: string | null;
};

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** Report generation + export from DossierPack (P5 / P5.1). */
export default function HubReportsPlaceholderPane() {
  const activeProjectId = useStore(useShallow((s) => s.activeProjectId));
  const openSettings = useStore((s) => s.openSettings);
  const envFlagsRevision = useStore((s) => s.envFlagsRevision);
  const [picked, setPicked] = useState<(typeof TEMPLATES)[number] | null>(null);
  const [prompt, setPrompt] = useState("");
  const [useLlm, setUseLlm] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shelfMsg, setShelfMsg] = useState<string | null>(null);
  const [exportCaps, setExportCaps] = useState<ExportCaps | null>(null);
  const [flagMap, setFlagMap] = useState<Partial<Record<ReportFlagAttr, boolean>> | null>(
    null,
  );
  const [flagsError, setFlagsError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    title: string;
    path: string;
    markdown: string;
    disclaimer?: string;
    template: string;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .listWikiReportTemplates()
      .then((body) => {
        if (!cancelled) setExportCaps(body.export ?? { md: true });
      })
      .catch(() => {
        if (!cancelled) setExportCaps({ md: true });
      });
    void api
      .getEnvFlags()
      .then((body) => {
        if (cancelled) return;
        const next: Partial<Record<ReportFlagAttr, boolean>> = {};
        for (const f of body.flags ?? []) {
          if ((REPORT_FLAG_ATTRS as readonly string[]).includes(f.attr)) {
            next[f.attr as ReportFlagAttr] = Boolean(f.value);
          }
        }
        setFlagMap(next);
        setFlagsError(null);
        // Drop stale flag-gate errors once the path is open.
        const allOn = REPORT_FLAG_ATTRS.every((k) => next[k] === true);
        if (allOn) {
          setError((prev) => (prev && isFlagGateError(prev) ? null : prev));
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setFlagMap(null);
          setFlagsError(formatApiError(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [envFlagsRevision]);

  const flagsReady = flagMap != null;
  const flagsMissing = useMemo(() => {
    if (!flagsReady) return [] as ReportFlagAttr[];
    return REPORT_FLAG_ATTRS.filter((k) => flagMap[k] !== true);
  }, [flagMap, flagsReady]);
  const reportPathReady = flagsReady && flagsMissing.length === 0;
  const showFlagCta = (flagsReady && flagsMissing.length > 0) || (!!error && isFlagGateError(error));

  const goEnvSettings = () => {
    const focus =
      (flagsMissing[0] as string | undefined) || "wiki_dossier_report_enabled";
    openSettings("env", { focusEnvAttr: focus });
  };

  const onGenerate = async () => {
    if (!picked) return;
    if (!activeProjectId) {
      setError("请先选择活动项目");
      return;
    }
    setBusy("generate");
    setError(null);
    setShelfMsg(null);
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
        template: picked.id,
      });
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const onExport = async (format: "md" | "docx" | "pdf" | "pptx") => {
    if (!activeProjectId || !picked) return;
    if (exportCaps && exportCaps[format] === false) {
      setError(`当前环境未安装 ${format.toUpperCase()} 导出依赖`);
      return;
    }
    setBusy(`export-${format}`);
    setError(null);
    try {
      const { blob, filename } = await api.exportWikiReport({
        project_id: activeProjectId,
        template: picked.id,
        format,
        prompt,
        use_llm: useLlm,
        ensure_dossier: true,
      });
      triggerDownload(blob, filename);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const onSaveShelf = async () => {
    if (!activeProjectId || !result?.markdown) return;
    setBusy("shelf");
    setShelfMsg(null);
    setError(null);
    try {
      const name = shelfFilename(
        `wiki_report_${result.template || "briefing"}`,
        "md",
      );
      await saveTextToProjectShelf(activeProjectId, name, result.markdown);
      setShelfMsg(`已保存到货架：${name}`);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const capLabel = (fmt: "md" | "docx" | "pdf" | "pptx") => {
    if (!exportCaps) return fmt.toUpperCase();
    if (exportCaps[fmt] === false) return `${fmt.toUpperCase()}(不可用)`;
    return fmt.toUpperCase();
  };

  return (
    <div className="flex flex-col gap-3 h-full min-h-0 overflow-y-auto" data-testid="hub-reports-pane">
      <p className="text-[11px] text-slate-500">
        文档生成基于<strong className="text-slate-400 font-normal"> 项目卷宗 DossierPack </strong>
        。灰度需开启 Wiki / 卷宗 / Report 旗标（见下方状态）。
        支持导出 Markdown / Word / PDF；Slide Deck 可导出 PPTX。
        也可从顶栏「产物」抽屉打开本页。
      </p>
      <div
        className="rounded-lg border border-edge/70 bg-ink/40 px-3 py-2 space-y-1.5"
        data-testid="hub-reports-flags"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
          <span className="text-slate-500">灰度旗标</span>
          {REPORT_FLAG_ATTRS.map((attr) => {
            const on = flagMap?.[attr];
            const mark = !flagsReady ? "?" : on ? "✓" : "×";
            const tone = !flagsReady
              ? "text-slate-500"
              : on
                ? "text-emerald-300"
                : "text-amber-300";
            return (
              <span key={attr} className={tone} data-testid={`hub-reports-flag-${attr}`}>
                {REPORT_FLAG_LABEL[attr]}
                {mark}
                <code className="ml-1 text-[9px] text-slate-600">{attr}</code>
              </span>
            );
          })}
        </div>
        {flagsError && (
          <p className="text-[10px] text-rose-300" data-testid="hub-reports-flags-error">
            无法读取旗标：{flagsError}
          </p>
        )}
        {flagsReady && reportPathReady && (
          <p className="text-[10px] text-emerald-400/90" data-testid="hub-reports-flags-ready">
            卷宗 Report 路径已开；可生成 / 导出 MD。
          </p>
        )}
        {showFlagCta && (
          <div className="flex flex-wrap items-center gap-2" data-testid="hub-reports-flags-cta">
            <p className="text-[10px] text-amber-200/90">
              {flagsReady && flagsMissing.length > 0
                ? `未开启：${flagsMissing.map((k) => REPORT_FLAG_LABEL[k]).join(" · ")}。灰度请在设置中打开。`
                : "当前错误疑似旗标关闭（409）。请到设置 → 环境变量开启相关项。"}
            </p>
            <button
              type="button"
              className="text-[10px] px-2 py-1 rounded border border-accent/50 text-accent hover:bg-accent/10"
              data-testid="hub-reports-open-env-settings"
              onClick={goEnvSettings}
            >
              去设置开启
            </button>
          </div>
        )}
      </div>
      <p className="text-[11px] text-slate-500" data-testid="hub-reports-dossier-hint">
        {activeProjectId
          ? `当前活动项目：${activeProjectId} · 生成前会自动 ensure 卷宗（若旗标已开）。`
          : "尚未选择活动项目；生成前需绑定 project_id 卷宗。"}
      </p>
      {exportCaps && (
        <p className="text-[10px] text-slate-600" data-testid="hub-reports-export-caps">
          导出能力：MD{exportCaps.md === false ? "×" : "✓"} · DOCX
          {exportCaps.docx ? "✓" : "×"} · PDF{exportCaps.pdf ? "✓" : "×"} · PPTX
          {exportCaps.pptx ? "✓" : "×"}
          {exportCaps.cjk_font ? ` · CJK ${exportCaps.cjk_font.split("/").pop()}` : ""}
        </p>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {TEMPLATES.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => {
              setPicked(t);
              setResult(null);
              setShelfMsg(null);
            }}
            className={`text-left border rounded-lg px-3 py-2.5 transition-colors ${
              picked?.id === t.id
                ? "border-accent/60 bg-accent/10"
                : "border-edge hover:border-accent/50 hover:bg-accent/5"
            }`}
          >
            <div className="text-sm text-slate-200">{t.title}</div>
            <p className="text-[11px] text-slate-500 mt-1">{t.desc}</p>
            <p className="text-[10px] text-accent/80 mt-1">{t.dossierSections}</p>
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
            提示词（可选）
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="例如：强调 VOC 与盐雾窗口…"
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
            LLM 执行摘要润色（需 wiki_dossier_llm_narrative）
          </label>
          {error && (
            <div
              className="text-xs text-rose-300 border border-rose-500/40 rounded px-2 py-1 space-y-1"
              data-testid="hub-reports-error"
            >
              <div>{error}</div>
              {isFlagGateError(error) && (
                <button
                  type="button"
                  className="text-[10px] px-2 py-0.5 rounded border border-accent/50 text-accent"
                  data-testid="hub-reports-error-open-env"
                  onClick={goEnvSettings}
                >
                  去设置开启旗标
                </button>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!!busy || !activeProjectId}
              className="px-3 py-1.5 rounded bg-accent text-ink text-sm disabled:opacity-50"
              data-testid="hub-reports-generate"
              onClick={() => void onGenerate()}
            >
              {busy === "generate" ? "生成中…" : "基于卷宗生成"}
            </button>
            {(["md", "docx", "pdf", "pptx"] as const).map((fmt) => {
              const unavailable = exportCaps?.[fmt] === false;
              return (
                <button
                  key={fmt}
                  type="button"
                  disabled={!!busy || !activeProjectId || unavailable}
                  className="px-2 py-1.5 rounded border border-edge text-xs text-slate-200 disabled:opacity-50"
                  data-testid={`hub-reports-export-${fmt}`}
                  title={
                    unavailable
                      ? `未安装 ${fmt} 导出依赖`
                      : fmt === "pptx" && picked.id !== "deck"
                        ? "任意模板也可导出 PPTX（按标题分页）"
                        : undefined
                  }
                  onClick={() => void onExport(fmt)}
                >
                  {busy === `export-${fmt}` ? `${fmt}…` : `导出 ${capLabel(fmt)}`}
                </button>
              );
            })}
          </div>
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
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={!!busy || !activeProjectId}
                data-testid="hub-reports-save-shelf"
                onClick={() => void onSaveShelf()}
                className="text-[10px] px-2 py-1 rounded border border-accent/40 text-accent disabled:opacity-40"
              >
                {busy === "shelf" ? "保存中…" : "保存到货架"}
              </button>
              <span
                className="text-[10px] text-amber-300 border border-amber-500/40 rounded px-1"
                data-testid="hub-reports-disclaimer"
              >
                {result.disclaimer || "draft_not_claims"}
              </span>
            </div>
          </div>
          {shelfMsg && (
            <p className="text-[10px] text-teal-300" data-testid="hub-reports-shelf-msg">
              {shelfMsg}
            </p>
          )}
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
