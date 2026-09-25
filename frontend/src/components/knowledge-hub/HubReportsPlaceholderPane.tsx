import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  api,
  awaitTaskStream,
  extractThinkingSteps,
  formatApiError,
  type ThinkingStep,
} from "../../api";
import { useStore } from "../../store";
import { saveTextToProjectShelf, shelfFilename } from "../../utils/export";
import { CANCEL_BUTTON_CLASS } from "../../hooks/useTaskCancel";
import ThinkingTimeline from "../ThinkingTimeline";
import WikiMarkdownReader from "../WikiMarkdownReader";

/** Grayscale keys required for Hub dossier → Report generate/export. */
const REPORT_FLAG_ATTRS = [
  "wiki_enabled",
  "wiki_project_dossier_enabled",
  "wiki_dossier_report_enabled",
] as const;

/** Extra flag for STORM longform (in addition to REPORT_FLAG_ATTRS). */
const STORM_FLAG_ATTR = "wiki_storm_report_enabled" as const;
/** Productization (default off) — Hub CTA only. */
const AUTO_PATCH_FLAG_ATTR = "wiki_dossier_auto_patch" as const;

type ReportFlagAttr = (typeof REPORT_FLAG_ATTRS)[number];
type TrackedFlagAttr = ReportFlagAttr | typeof STORM_FLAG_ATTR | typeof AUTO_PATCH_FLAG_ATTR;

const REPORT_FLAG_LABEL: Record<TrackedFlagAttr, string> = {
  wiki_enabled: "Wiki",
  wiki_project_dossier_enabled: "卷宗",
  wiki_dossier_report_enabled: "Report",
  wiki_storm_report_enabled: "STORM",
  wiki_dossier_auto_patch: "自动patch",
};

function isFlagGateError(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("wiki_dossier_report_enabled") ||
    m.includes("wiki_project_dossier_enabled") ||
    m.includes("wiki_storm_report_enabled") ||
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
  const [flagMap, setFlagMap] = useState<Partial<Record<TrackedFlagAttr, boolean>> | null>(
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
  const [stormTopic, setStormTopic] = useState("");
  const [stormUseLlm, setStormUseLlm] = useState(false);
  const [stormParallel, setStormParallel] = useState(false);
  const [stormProgress, setStormProgress] = useState(0);
  const [stormStage, setStormStage] = useState("");
  const [stormThinking, setStormThinking] = useState<ThinkingStep[]>([]);
  const stormAbortRef = useRef<(AbortController & { taskId?: string }) | null>(null);

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
        const next: Partial<Record<TrackedFlagAttr, boolean>> = {};
        const tracked = [...REPORT_FLAG_ATTRS, STORM_FLAG_ATTR, AUTO_PATCH_FLAG_ATTR] as const;
        for (const f of body.flags ?? []) {
          if ((tracked as readonly string[]).includes(f.attr)) {
            next[f.attr as TrackedFlagAttr] = Boolean(f.value);
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
  const stormReady =
    reportPathReady && flagMap?.[STORM_FLAG_ATTR] === true;
  const autoPatchOn = flagsReady && flagMap?.[AUTO_PATCH_FLAG_ATTR] === true;
  const showFlagCta = (flagsReady && flagsMissing.length > 0) || (!!error && isFlagGateError(error));

  const goEnvSettings = () => {
    const focus =
      (flagsMissing[0] as string | undefined) ||
      (error?.includes("wiki_storm_report_enabled")
        ? STORM_FLAG_ATTR
        : "wiki_dossier_report_enabled");
    openSettings("env", { focusEnvAttr: focus });
  };

  const cancelStorm = async () => {
    const abort = stormAbortRef.current;
    if (!abort) return;
    abort.abort();
    const tid = abort.taskId;
    if (tid) {
      try {
        await api.cancelTask(tid);
      } catch {
        /* best-effort */
      }
    }
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

  const onStormExport = async (format: "md" | "docx" | "pdf" | "pptx") => {
    if (!activeProjectId) return;
    if (exportCaps && exportCaps[format] === false) {
      setError(`当前环境未安装 ${format.toUpperCase()} 导出依赖`);
      return;
    }
    setBusy(`storm-export-${format}`);
    setError(null);
    try {
      const { blob, filename } = await api.exportWikiStormReport({
        project_id: activeProjectId,
        format,
        regenerate: false,
        topic: stormTopic.trim(),
        use_llm: stormUseLlm,
        parallel: stormParallel,
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

  const onStormGenerate = async () => {
    if (!activeProjectId) {
      setError("请先选择活动项目");
      return;
    }
    setBusy("storm");
    setError(null);
    setShelfMsg(null);
    setStormProgress(0.05);
    setStormStage("queued");
    setStormThinking([]);
    const ctrl = new AbortController() as AbortController & { taskId?: string };
    stormAbortRef.current = ctrl;
    try {
      const accepted = await api.startWikiStormReport({
        project_id: activeProjectId,
        topic: stormTopic.trim(),
        max_sections: 6,
        use_llm: stormUseLlm,
        parallel: stormParallel,
        ensure_dossier: true,
        persist: true,
      });
      ctrl.taskId = accepted.task_id;
      await awaitTaskStream(
        accepted.task_id,
        (ev) => {
          setStormProgress(ev.progress ?? 0);
          setStormStage(ev.stage || ev.message || "");
          const steps = extractThinkingSteps(ev);
          if (steps.length) setStormThinking(steps);
        },
        0,
        ctrl.signal,
        180_000,
      );
      const page = await api.getWikiStormReport(activeProjectId);
      setResult({
        title: page.title || "STORM 长文",
        path: page.path,
        markdown: page.markdown,
        disclaimer: page.disclaimer || accepted.disclaimer || "draft_not_claims",
        template: "storm",
      });
      setStormProgress(1);
      setStormStage("done");
    } catch (e) {
      const msg = formatApiError(e);
      if (!ctrl.signal.aborted) setError(msg);
      else setStormStage("cancelled");
    } finally {
      stormAbortRef.current = null;
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
          {([...REPORT_FLAG_ATTRS, STORM_FLAG_ATTR, AUTO_PATCH_FLAG_ATTR] as const).map((attr) => {
            const on = flagMap?.[attr];
            const mark = !flagsReady ? "?" : on ? "✓" : "×";
            const tone = !flagsReady
              ? "text-slate-500"
              : on
                ? "text-emerald-300"
                : attr === AUTO_PATCH_FLAG_ATTR
                  ? "text-slate-500"
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
        {flagsReady && !autoPatchOn && (
          <div
            className="flex flex-wrap items-center gap-2"
            data-testid="hub-reports-auto-patch-cta"
          >
            <p className="text-[10px] text-slate-500">
              事件自动 patch 默认关（白名单：入库/DOE/台账/闭环等；未知事件跳过）。需要飞轮时再开。
            </p>
            <button
              type="button"
              className="text-[10px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/50 hover:text-accent"
              data-testid="hub-reports-auto-patch-open-env"
              onClick={() => openSettings("env", { focusEnvAttr: AUTO_PATCH_FLAG_ATTR })}
            >
              去设置开自动 patch
            </button>
          </div>
        )}
        {flagsReady && autoPatchOn && (
          <p className="text-[10px] text-amber-300/90" data-testid="hub-reports-auto-patch-on">
            自动 patch 已开 · 仅白名单事件刷新对应节（不洗表格 / 不进 Claims）
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

      <div
        className="border border-violet-500/30 rounded-lg p-3 space-y-2 bg-violet-500/5"
        data-testid="hub-reports-storm"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm text-slate-100">STORM 长文（异步）</h3>
            <p className="text-[11px] text-slate-500 mt-0.5">
              大纲 → 分章 → 缝合；落盘 `reports/*-storm.md` ·{" "}
              <span className="text-amber-300/90">draft_not_claims</span>
              （不进 Claims/DOE）· 默认已开旗标；受 max_sections 成本帽
            </p>
          </div>
          {!stormReady && flagsReady && (
            <button
              type="button"
              className="text-[10px] px-2 py-1 rounded border border-accent/50 text-accent"
              data-testid="hub-reports-storm-open-env"
              onClick={() => openSettings("env", { focusEnvAttr: STORM_FLAG_ATTR })}
            >
              开启 STORM 旗标
            </button>
          )}
        </div>
        <label className="block text-[11px] text-slate-400">
          主题（可选）
          <input
            value={stormTopic}
            onChange={(e) => setStormTopic(e.target.value)}
            placeholder="例如：硅烷转化膜盐雾 720h 技术可行性"
            className="w-full mt-1 bg-ink border border-edge rounded px-2 py-1.5 text-sm text-slate-200"
            data-testid="hub-reports-storm-topic"
            disabled={!stormReady || !!busy}
          />
        </label>
        <label className="flex items-center gap-2 text-[11px] text-slate-400">
          <input
            type="checkbox"
            checked={stormUseLlm}
            onChange={(e) => setStormUseLlm(e.target.checked)}
            data-testid="hub-reports-storm-use-llm"
            disabled={!stormReady || !!busy}
          />
          使用 LLM 分章（关则确定性离线草稿）
        </label>
        <label className="flex items-center gap-2 text-[11px] text-slate-400">
          <input
            type="checkbox"
            checked={stormParallel}
            onChange={(e) => setStormParallel(e.target.checked)}
            data-testid="hub-reports-storm-parallel"
            disabled={!stormReady || !!busy}
          />
          有限并行分章（depends_on 波次；覆盖服务端 wiki_storm_parallel）
        </label>
        {(busy === "storm" || stormThinking.length > 0) && (
          <div className="space-y-1.5" data-testid="hub-reports-storm-progress">
            <div className="flex items-center justify-between text-[10px] text-slate-500">
              <span>{stormStage || "queued"}</span>
              <span>{Math.round(stormProgress * 100)}%</span>
            </div>
            <div className="h-1.5 rounded bg-ink overflow-hidden border border-edge/40">
              <div
                className="h-full bg-violet-400/80 transition-all"
                style={{ width: `${Math.min(100, Math.round(stormProgress * 100))}%` }}
              />
            </div>
            <ThinkingTimeline steps={stormThinking} title="STORM 进度" compact />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={!!busy || !activeProjectId || !stormReady}
            className="px-3 py-1.5 rounded bg-violet-500/90 text-ink text-sm disabled:opacity-50"
            data-testid="hub-reports-storm-generate"
            onClick={() => void onStormGenerate()}
          >
            {busy === "storm" ? "STORM 生成中…" : "生成 STORM 长文"}
          </button>
          {busy === "storm" && (
            <button
              type="button"
              className={CANCEL_BUTTON_CLASS}
              data-testid="hub-reports-storm-cancel"
              onClick={() => void cancelStorm()}
            >
              ✕ 取消
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-2" data-testid="hub-reports-storm-exports">
          {(["md", "docx", "pdf", "pptx"] as const).map((fmt) => {
            const unavailable = exportCaps?.[fmt] === false;
            return (
              <button
                key={fmt}
                type="button"
                disabled={!!busy || !activeProjectId || !stormReady || unavailable}
                className="px-2 py-1.5 rounded border border-violet-500/40 text-xs text-slate-200 disabled:opacity-50"
                data-testid={`hub-reports-storm-export-${fmt}`}
                title={
                  unavailable
                    ? `未安装 ${fmt} 导出依赖`
                    : "导出已落盘的 STORM 长文（无则先生成）"
                }
                onClick={() => void onStormExport(fmt)}
              >
                {busy === `storm-export-${fmt}` ? `${fmt}…` : `导出 ${capLabel(fmt)}`}
              </button>
            );
          })}
        </div>
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
