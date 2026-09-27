/**
 * W3-7: 表格抽取 UI badge —— 在资料详情中展示 TableAsset 的 kind 标签
 * （recipe/performance/tds_sds）+ 行列预览；PropertySet（W3-1）存在时展示
 * 归一化属性，缺字段时降级显示。
 */
import { useEffect, useState } from "react";
import { api, formatApiError, type TableAssetView, type TablePropertyView } from "../api";

const KIND_META: Record<string, { label: string; icon: string; cls: string }> = {
  recipe: {
    label: "配方表",
    icon: "🧪",
    cls: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
  },
  performance: {
    label: "性能表",
    icon: "📊",
    cls: "text-sky-300 border-sky-500/40 bg-sky-500/10",
  },
  tds_sds: {
    label: "TDS/SDS",
    icon: "📋",
    cls: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  },
  other: {
    label: "表格",
    icon: "📄",
    cls: "text-slate-400 border-edge bg-ink/30",
  },
};

function kindMeta(kind?: string) {
  return KIND_META[kind ?? ""] ?? KIND_META.other;
}

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v);
}

function propertyText(p: TablePropertyView): string {
  const name = p.name_normalized || p.name || "—";
  const value =
    p.value !== null && p.value !== undefined && p.value !== ""
      ? String(p.value)
      : (p.raw_text ?? "");
  const unit = p.unit_normalized || p.unit || "";
  return `${name}: ${value}${unit ? ` ${unit}` : ""}`.trim();
}

const PREVIEW_ROWS = 3;

export default function TableBadges({ sourceId }: { sourceId: string }) {
  const [tables, setTables] = useState<TableAssetView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setTables(null);
    setError(null);
    api
      .getSourceTables(sourceId)
      .then((res) => {
        if (!cancelled) setTables(Array.isArray(res?.tables) ? res.tables : []);
      })
      .catch((e) => {
        if (!cancelled) setError(formatApiError(e));
      });
    return () => {
      cancelled = true;
    };
  }, [sourceId]);

  if (tables === null && !error) {
    return (
      <div className="text-[11px] text-slate-500" data-testid="table-badges-loading">
        表格抽取结果加载中…
      </div>
    );
  }
  if (error) {
    return (
      <div className="text-[11px] text-slate-600" data-testid="table-badges-error">
        表格信息暂不可用
      </div>
    );
  }
  if (!tables || tables.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2" data-testid="table-badges">
      <div className="text-[11px] font-medium text-slate-400">
        📊 抽取表格 <span className="text-slate-600">({tables.length})</span>
      </div>
      {tables.map((t, i) => {
        const meta = kindMeta(t.kind);
        const headers = Array.isArray(t.headers) ? t.headers : [];
        const rows = Array.isArray(t.rows) ? t.rows : [];
        const props = t.property_set?.properties;
        const warnings = t.property_set?.warnings;
        return (
          <div
            key={t.table_id || `table-${i}`}
            className="border border-edge rounded p-2 bg-ink/20"
            data-testid={`table-badge-${i}`}
          >
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded border ${meta.cls}`}
                data-testid={`table-kind-${i}`}
              >
                {meta.icon} {meta.label}
              </span>
              {t.page_no != null && (
                <span className="text-[10px] font-mono text-slate-500">p.{t.page_no}</span>
              )}
              {t.caption && (
                <span className="text-[11px] text-slate-300 truncate" title={t.caption}>
                  {t.caption}
                </span>
              )}
            </div>

            {headers.length > 0 && (
              <table className="mt-1.5 w-full text-[10px] text-slate-400 border-collapse">
                <thead>
                  <tr>
                    {headers.map((h, hi) => (
                      <th
                        key={hi}
                        className="text-left font-medium border-b border-edge/60 pr-2 py-0.5 truncate max-w-[10rem]"
                      >
                        {cellText(h)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, PREVIEW_ROWS).map((r, ri) => (
                    <tr key={ri} className="border-b border-edge/30">
                      {(Array.isArray(r) ? r : []).slice(0, headers.length).map((c, ci) => (
                        <td key={ci} className="pr-2 py-0.5 truncate max-w-[10rem]">
                          {cellText(c)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {rows.length > PREVIEW_ROWS && (
              <div className="text-[10px] text-slate-600 mt-0.5">
                …共 {rows.length} 行，仅预览前 {PREVIEW_ROWS} 行
              </div>
            )}

            {Array.isArray(props) && props.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1.5" data-testid={`table-props-${i}`}>
                {props.slice(0, 12).map((p, pi) => (
                  <span
                    key={pi}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20"
                    title={p.raw_text || undefined}
                  >
                    {propertyText(p)}
                  </span>
                ))}
                {props.length > 12 && (
                  <span className="text-[10px] text-slate-600">+{props.length - 12}</span>
                )}
              </div>
            )}
            {Array.isArray(warnings) && warnings.length > 0 && (
              <div className="mt-1 text-[10px] text-amber-400/80">
                ⚠ {warnings.slice(0, 3).join("；")}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
