import { useEffect, useState } from "react";
import { api, formatApiError, type KBExtractionFormula, type KBExtractionTable } from "../api";

/**
 * Up-5A: 展示 MinerU 结构化解析写入 extraction_tables / extraction_formulas 的内容。
 * 只读。bbox 字段真实性待 MinerU 真实样本验证，UI 上标注。
 */
export default function ExtractionTablesPanel({ sourceId }: { sourceId: string }) {
  const [tables, setTables] = useState<KBExtractionTable[]>([]);
  const [formulas, setFormulas] = useState<KBExtractionFormula[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [t, f] = await Promise.all([
          api.getSourceExtractionTables(sourceId).catch(() => [] as KBExtractionTable[]),
          api.getSourceExtractionFormulas(sourceId).catch(() => [] as KBExtractionFormula[]),
        ]);
        if (!cancelled) {
          setTables(t ?? []);
          setFormulas(f ?? []);
        }
      } catch (e) {
        if (!cancelled) setError(formatApiError(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceId]);

  if (loading) {
    return <p className="text-xs text-slate-500 py-6 text-center">加载结构化抽取结果…</p>;
  }
  if (error) {
    return <p className="text-xs text-rose-400 py-4 text-center">{error}</p>;
  }
  if (tables.length === 0 && formulas.length === 0) {
    return (
      <p className="text-xs text-slate-500 py-6 text-center">
        该来源暂无结构化表格/公式（仅 MinerU 解析的文档会写入）。
      </p>
    );
  }

  return (
    <div className="space-y-4 max-h-[55vh] overflow-y-auto pr-1" data-testid="extraction-tables-panel">
      {tables.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">
            表格 · {tables.length}
          </div>
          <div className="space-y-2">
            {tables.map((t) => (
              <div key={t.id} className="rounded border border-edge bg-ink/30 p-2">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mb-1.5">
                  {t.page_no != null && (
                    <span className="text-[10px] font-mono text-slate-500">第 {t.page_no} 页</span>
                  )}
                  {t.n_rows != null && t.n_cols != null && (
                    <span className="text-[10px] text-slate-500">
                      {t.n_rows} 行 × {t.n_cols} 列
                    </span>
                  )}
                  {t.bbox && (
                    <span className="text-[10px] text-amber-400/70" title="bbox 真实性待 MinerU 真实样本验证">
                      bbox 待验证
                    </span>
                  )}
                </div>
                {t.caption && (
                  <div className="text-[11px] text-slate-400 mb-1.5">表注：{t.caption}</div>
                )}
                <pre className="text-[11px] text-slate-300 whitespace-pre-wrap leading-relaxed overflow-x-auto">
                  {t.markdown_text}
                </pre>
              </div>
            ))}
          </div>
        </div>
      )}
      {formulas.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">
            公式 · {formulas.length}
          </div>
          <div className="space-y-2">
            {formulas.map((f) => (
              <div key={f.id} className="rounded border border-edge bg-ink/30 p-2">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mb-1">
                  {f.formula_no && (
                    <span className="text-[10px] font-mono text-accent2">({f.formula_no})</span>
                  )}
                  {f.page_no != null && (
                    <span className="text-[10px] font-mono text-slate-500">第 {f.page_no} 页</span>
                  )}
                  {f.bbox && (
                    <span className="text-[10px] text-amber-400/70" title="bbox 真实性待 MinerU 真实样本验证">
                      bbox 待验证
                    </span>
                  )}
                </div>
                <code className="text-[11px] text-slate-200 whitespace-pre-wrap break-all">
                  {f.latex}
                </code>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
