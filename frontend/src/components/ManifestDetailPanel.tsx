import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "../api";
import { saveTextToProjectShelf, shelfFilename } from "../utils/export";
import SourceDetailModal from "./SourceDetailModal";

export type ManifestItem = {
  id: string;
  title?: string;
  doi?: string | null;
  screening?: string;
  snippet?: string;
  source?: string;
  evidence_class?: string;
  has_fulltext?: boolean;
  enrich_status?: string;
  oa_pdf_url?: string;
  /** W4-6 引用定位器（只读展示，无写入口）。 */
  locator?: { page?: number; figure?: string; table?: string };
};

export type Manifest = {
  project_id: string;
  items: ManifestItem[];
  frozen: { at: number; actor: string; item_ids: string[]; digest: string } | null;
  coverage: { candidate_count: number; frozen_count: number };
};

export type ManifestStyleKey = "list" | "citation" | "table";

export const MANIFEST_STYLES: Array<{ key: ManifestStyleKey; label: string }> = [
  { key: "list", label: "列表" },
  { key: "citation", label: "引用" },
  { key: "table", label: "表格" },
];

export function hasFulltext(it: ManifestItem): boolean {
  return !!it.has_fulltext || it.enrich_status === "fetched";
}

/** locator 只读展示，如 "p.3 / Fig.2 / Tab.1"；无则返回空串。 */
export function formatLocator(it: ManifestItem): string {
  const loc = it.locator;
  if (!loc) return "";
  const parts: string[] = [];
  if (loc.page != null) parts.push(`p.${loc.page}`);
  if (loc.figure) parts.push(`Fig.${loc.figure}`);
  if (loc.table) parts.push(`Tab.${loc.table}`);
  return parts.join(" / ");
}

/** 七格统计：纯函数，便于单测。 */
export function computeManifestCoverage(
  items: ManifestItem[],
  frozen: boolean,
): Array<{ label: string; value: number }> {
  const n = items.length;
  const doi = items.filter((i) => (i.doi || "").trim()).length;
  const snippet = items.filter((i) => (i.snippet || "").trim()).length;
  const full = items.filter(hasFulltext).length;
  const match = items.filter((i) => i.screening === "match").length;
  const noMatch = items.filter((i) => i.screening === "no_match").length;
  const pending = n - match - noMatch;
  return [
    { label: frozen ? "冻结条目" : "候选条目", value: n },
    { label: "有 DOI", value: doi },
    { label: "有摘要", value: snippet },
    { label: "有全文", value: full },
    { label: "筛选命中", value: match },
    { label: "筛选排除", value: noMatch },
    { label: "待筛选", value: pending },
  ];
}

function cell(text: string): string {
  return text.replace(/\|/g, "｜").replace(/\n/g, " ").slice(0, 120);
}

/** 按样式生成 Markdown 快照：纯函数，便于单测。 */
export function buildManifestMarkdown(
  style: ManifestStyleKey,
  heading: string,
  items: ManifestItem[],
): string {
  const lines = [`# ${heading}`, ""];
  if (style === "citation") {
    items.forEach((it, i) => {
      const loc = formatLocator(it);
      lines.push(
        `[${i + 1}] ${it.title || it.id}${it.doi ? `. DOI: ${it.doi}` : ""}${loc ? ` (${loc})` : ""}`,
      );
    });
  } else if (style === "table") {
    lines.push("| # | 标题 | DOI | 来源 | 筛选 | 全文 |", "|---|---|---|---|---|---|");
    items.forEach((it, i) => {
      lines.push(
        `| ${i + 1} | ${cell(it.title || it.id)} | ${cell(it.doi || "—")} | ` +
          `${cell(it.source || it.evidence_class || "—")} | ${cell(it.screening || "unset")} | ` +
          `${hasFulltext(it) ? "✓" : "—"} |`,
      );
    });
  } else {
    items.forEach((it, i) => {
      lines.push(`## ${i + 1}. ${it.title || it.id}`);
      if (it.doi) lines.push(`DOI: ${it.doi}`);
      const loc = formatLocator(it);
      if (loc) lines.push(`定位: ${loc}`);
      lines.push(
        `筛选: ${it.screening || "unset"} · 来源: ${it.source || it.evidence_class || "—"} · 全文: ${hasFulltext(it) ? "有" : "无"}`,
      );
      if ((it.snippet || "").trim()) lines.push(`> ${it.snippet}`);
      lines.push("");
    });
  }
  return lines.join("\n");
}

function screeningTone(screening?: string): string {
  if (screening === "match") return "text-emerald-400 border-emerald-500/40";
  if (screening === "no_match") return "text-rose-400 border-rose-500/40";
  return "text-amber-300 border-amber-500/40";
}

type Props = {
  projectId: string | null;
};

/**
 * W4-5 (P1-24): Manifest 前端详情面板。
 * - 引用列表 + 点击打开 SourceDetailModal（Wave 3 复用）。
 * - "Frozen review corpus" coverage 七格统计（诚实声明：计数 = 冻结时返回给 Agent 的内容）。
 * - 换样式 → 预览 → 存新版（快照存到项目 shelf，不修改 manifest 本体）。
 * - 条目只读：不提供任何编辑入口（防篡改证据）。
 */
export default function ManifestDetailPanel({ projectId }: Props) {
  const [man, setMan] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ManifestItem | null>(null);
  const [style, setStyle] = useState<ManifestStyleKey>("list");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  /** F-4: 序号守卫 —— project 快速切换时旧请求的 manifest 不覆盖新数据。 */
  const loadSeq = useRef(0);

  const reload = useCallback(async () => {
    const seq = ++loadSeq.current;
    if (!projectId) {
      setMan(null);
      return;
    }
    try {
      const m = (await api.getLiteratureManifest(projectId)) as unknown as Manifest;
      if (loadSeq.current !== seq) return;
      setMan(m);
      setError(null);
    } catch (e) {
      if (loadSeq.current !== seq) return;
      setError(formatApiError(e));
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const frozen = man?.frozen ?? null;
  const scopedItems = useMemo(() => {
    if (!man) return [];
    if (!frozen) return man.items;
    // F-3: item_ids 缺失（旧数据/损坏）时兜底空数组，防白屏。
    const ids = new Set(frozen.item_ids ?? []);
    return man.items.filter((it) => ids.has(it.id));
  }, [man, frozen]);

  const cells = useMemo(
    () => computeManifestCoverage(scopedItems, !!frozen),
    [scopedItems, frozen],
  );

  const digestShort = frozen?.digest ? frozen.digest.slice(0, 10) : "—";
  const frozenAt = frozen?.at
    ? new Date(frozen.at * 1000).toLocaleString("zh-CN", { hour12: false })
    : "—";

  const saveSnapshot = async () => {
    if (!projectId || saving) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const md = buildManifestMarkdown(
        style,
        `文献 Manifest 快照（digest ${digestShort}）`,
        scopedItems,
      );
      const filename = shelfFilename(`manifest_${digestShort}`, "md");
      await saveTextToProjectShelf(projectId, filename, md);
      setSaveMsg(`已存新版：${filename}`);
    } catch (e) {
      setSaveMsg(`保存失败：${formatApiError(e)}`);
    } finally {
      setSaving(false);
    }
  };

  if (!projectId) {
    return (
      <div
        className="rounded border border-edge px-2 py-1.5 text-[11px] text-slate-500"
        data-testid="manifest-detail-panel"
      >
        文献 Manifest：请先选择活动项目
      </div>
    );
  }

  return (
    <div
      className="rounded border border-edge/70 bg-ink/40 px-3 py-2 space-y-2 text-[11px]"
      data-testid="manifest-detail-panel"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-slate-200 font-medium">文献 Manifest 详情</span>
        {frozen ? (
          <span className="text-slate-400" data-testid="manifest-frozen-meta">
            已冻结 · {frozenAt} · {frozen.actor} · digest {digestShort}
          </span>
        ) : (
          <span className="text-amber-300/90" data-testid="manifest-frozen-meta">
            未冻结：当前为候选集
          </span>
        )}
        <button
          type="button"
          className="ml-auto px-1.5 py-0.5 rounded border border-edge text-slate-400 hover:border-accent/40"
          data-testid="manifest-reload-btn"
          onClick={() => void reload()}
        >
          刷新
        </button>
      </div>

      {/* 七格统计 */}
      <div
        className="grid grid-cols-7 gap-1"
        data-testid="manifest-coverage"
        aria-label="Frozen review corpus coverage"
      >
        {cells.map((c, i) => (
          <div
            key={c.label}
            className="rounded border border-edge/60 bg-ink/60 px-1 py-1 text-center"
            data-testid={`manifest-coverage-cell-${i}`}
          >
            <div className="text-slate-100 font-medium text-[12px]">{c.value}</div>
            <div className="text-slate-500 text-[10px]">{c.label}</div>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-slate-500" data-testid="manifest-honesty">
        诚实声明：以上计数 = 冻结时返回给 Agent 的文献集合（frozen review
        corpus），不是人工已逐篇读完。
      </p>

      {/* 引用列表（只读） */}
      <div className="space-y-1" data-testid="manifest-item-list">
        <div className="text-slate-400">引用列表（{scopedItems.length}）· 只读</div>
        {scopedItems.length === 0 && (
          <p className="text-slate-500">暂无条目：先在上方文献冻结条 Capture / Freeze。</p>
        )}
        <ul className="max-h-56 overflow-auto space-y-1">
          {scopedItems.map((it) => (
            <li
              key={it.id}
              className="rounded border border-edge/50 px-2 py-1 flex flex-wrap items-center gap-2"
              data-testid={`manifest-item-${it.id}`}
            >
              <button
                type="button"
                className="text-left text-sky-300 hover:underline flex-1 min-w-0 truncate"
                title={it.title || it.id}
                data-testid={`manifest-item-open-${it.id}`}
                onClick={() => setDetail(it)}
              >
                {(it.title || it.id).slice(0, 80)}
              </button>
              {it.doi && (
                <span className="text-slate-500 text-[10px] truncate max-w-40" title={it.doi}>
                  {it.doi}
                </span>
              )}
              {formatLocator(it) && (
                <span
                  className="text-violet-300/90 text-[10px]"
                  title="引用定位器（只读）"
                  data-testid={`manifest-item-locator-${it.id}`}
                >
                  {formatLocator(it)}
                </span>
              )}
              <span
                className={`px-1 rounded border text-[10px] ${screeningTone(it.screening)}`}
              >
                {it.screening || "unset"}
              </span>
              {hasFulltext(it) && (
                <span className="px-1 rounded border border-sky-500/40 text-sky-300 text-[10px]">
                  全文
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>

      {/* 换样式 → 预览 → 存新版 */}
      <div className="border-t border-edge/60 pt-1.5 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-slate-400">样式</span>
          {MANIFEST_STYLES.map((s) => (
            <button
              key={s.key}
              type="button"
              data-testid={`manifest-style-${s.key}`}
              onClick={() => setStyle(s.key)}
              className={`px-1.5 py-0.5 rounded border ${
                style === s.key
                  ? "border-accent/60 text-slate-100"
                  : "border-edge text-slate-400 hover:border-accent/40"
              }`}
            >
              {s.label}
            </button>
          ))}
          <button
            type="button"
            disabled={saving || scopedItems.length === 0}
            className="ml-auto px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-200 disabled:opacity-40"
            data-testid="manifest-save-btn"
            onClick={() => void saveSnapshot()}
          >
            {saving ? "保存中…" : "存新版"}
          </button>
        </div>
        <div
          className="rounded border border-edge/50 bg-ink/60 px-2 py-1.5 max-h-48 overflow-auto"
          data-testid="manifest-style-preview"
        >
          <div className="text-slate-500 text-[10px] mb-1">
            预览（{MANIFEST_STYLES.find((s) => s.key === style)?.label}样式 · 只读，不修改
            Manifest）
          </div>
          {style === "citation" && (
            <ol className="space-y-0.5 text-slate-300">
              {scopedItems.map((it, i) => (
                <li key={it.id}>
                  <span className="text-slate-500">[{i + 1}]</span> {it.title || it.id}
                  {it.doi && <span className="text-slate-500"> · DOI: {it.doi}</span>}
                  {formatLocator(it) && (
                    <span className="text-violet-300/90"> · {formatLocator(it)}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
          {style === "table" && (
            <table className="w-full text-slate-300">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="pr-2">#</th>
                  <th className="pr-2">标题</th>
                  <th className="pr-2">DOI</th>
                  <th className="pr-2">筛选</th>
                  <th>全文</th>
                </tr>
              </thead>
              <tbody>
                {scopedItems.map((it, i) => (
                  <tr key={it.id} className="border-t border-edge/40">
                    <td className="pr-2">{i + 1}</td>
                    <td className="pr-2">{(it.title || it.id).slice(0, 40)}</td>
                    <td className="pr-2 text-slate-500">{it.doi || "—"}</td>
                    <td className="pr-2">{it.screening || "unset"}</td>
                    <td>{hasFulltext(it) ? "✓" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {style === "list" && (
            <ul className="space-y-1 text-slate-300">
              {scopedItems.map((it, i) => (
                <li key={it.id}>
                  <span className="text-slate-500">{i + 1}.</span> {it.title || it.id}
                  <div className="text-slate-500 text-[10px] truncate">
                    {(it.snippet || "").slice(0, 120) || "（无摘要）"}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        {saveMsg && (
          <p className="text-[10px] text-slate-400" data-testid="manifest-save-msg">
            {saveMsg}
          </p>
        )}
      </div>

      {error && (
        <p className="text-rose-400" data-testid="manifest-error">
          {error}
        </p>
      )}

      {detail && (
        <SourceDetailModal
          title={detail.title || detail.id}
          sourceId={detail.id}
          focusPage={detail.locator?.page ?? null}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}
