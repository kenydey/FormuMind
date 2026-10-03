/**
 * W3-13: 技术报告导出按钮 —— kind + format 选择 → POST /api/reports/export → 下载文件。
 * 挂载点: FormulaLeaderboard(kind=formulation) / DoeResultsPanel(kind=doe, optimization)。
 */
import { useState } from "react";
import { api, formatApiError } from "../api";
import { useStore } from "../store";
import { saveBlob } from "../utils/download";

export type TechReportKind = "formulation" | "doe" | "optimization";
export type TechReportFormat = "docx" | "pdf" | "html" | "md";

const KIND_LABEL: Record<TechReportKind, string> = {
  formulation: "配方技术报告",
  doe: "DOE技术报告",
  optimization: "优化技术报告",
};

const FORMATS: { value: TechReportFormat; label: string }[] = [
  { value: "docx", label: "DOCX" },
  { value: "pdf", label: "PDF" },
  { value: "html", label: "HTML" },
  { value: "md", label: "MD" },
];

export default function TechReportExportButton({ kind }: { kind: TechReportKind }) {
  const activeProjectId = useStore((s) => s.activeProjectId);
  const [format, setFormat] = useState<TechReportFormat>("docx");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function onExport() {
    if (!activeProjectId) {
      setError("请先打开或新建项目");
      return;
    }
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const { blob, filename } = await api.exportTechReport({
        kind,
        format,
        project_id: activeProjectId,
      });
      saveBlob(blob, filename);
      setDone(`已导出 ${filename}`);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span
      className="inline-flex items-center gap-1.5"
      data-testid={`tech-report-export-${kind}`}
    >
      <select
        value={format}
        onChange={(e) => setFormat(e.target.value as TechReportFormat)}
        className="bg-ink border border-edge rounded px-1.5 py-1 text-[11px] text-slate-300"
        title="导出格式"
        data-testid={`tech-report-format-${kind}`}
        disabled={busy}
      >
        {FORMATS.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => void onExport()}
        disabled={busy}
        className="text-[11px] border border-edge rounded px-2 py-1 text-slate-300 hover:text-accent hover:border-accent/50 disabled:opacity-50"
        title={`导出${KIND_LABEL[kind]}（经发布预检门禁）`}
        data-testid={`tech-report-export-btn-${kind}`}
      >
        {busy ? "导出中…" : "📄 导出技术报告"}
      </button>
      {error && (
        <span className="text-[10px] text-rose-400" data-testid={`tech-report-error-${kind}`}>
          {error}
        </span>
      )}
      {done && (
        <span className="text-[10px] text-emerald-400" data-testid={`tech-report-done-${kind}`}>
          ✓ {done}
        </span>
      )}
    </span>
  );
}
