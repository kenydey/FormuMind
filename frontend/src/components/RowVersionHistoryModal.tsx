import { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "../api";

interface VersionEntry {
  id: string;
  version: number;
  action?: string;
  timestamp?: string;
  creator?: string | null;
}

interface RowVersionHistoryModalProps {
  campaignId: number;
  rowId: number;
  onClose: () => void;
  onRestored?: () => void;
}

/**
 * P3: DataLab auto-saved version history for one DOE workbench row.
 *
 * The platform snapshots the item on every save (workbench sync → save-item),
 * so this is a zero-effort audit trail of how a row's params/measurements
 * evolved. Each entry can be diffed against the previous one, or restored
 * (the platform mints a new "restored" version, keeping the operation
 * reversible).
 */
export default function RowVersionHistoryModal({
  campaignId,
  rowId,
  onClose,
  onRestored,
}: RowVersionHistoryModalProps) {
  const [versions, setVersions] = useState<VersionEntry[]>([]);
  const [refcode, setRefcode] = useState("");
  const [error, setError] = useState("");
  const [restoring, setRestoring] = useState<string | null>(null);
  const [diff, setDiff] = useState<{ v1: number; v2: number; summary: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .getWorkbenchVersions(campaignId, rowId)
      .then((res) => {
        setRefcode(res.refcode);
        setVersions(res.versions || []);
      })
      .catch((e) => setError(formatApiError(e)));
  }, [campaignId, rowId]);

  useEffect(() => {
    load();
  }, [load]);

  async function restore(v: VersionEntry) {
    const msg = `将行 #${rowId} 恢复到 v${v.version}（${v.timestamp ? v.timestamp.replace("T", " ").slice(0, 16) : ""}）？\n\n将覆盖该行当前的参数/测量/状态。平台会立即留一个新版本，可再次恢复——此操作可逆。`;
    if (!window.confirm(msg)) return;
    setRestoring(v.id);
    setError("");
    try {
      await api.restoreWorkbenchVersion(campaignId, rowId, v.id);
      await load();
      onRestored?.();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setRestoring(null);
    }
  }

  async function compare(v: VersionEntry) {
    setBusy(true);
    setError("");
    setDiff(null);
    try {
      const prev = versions.find((x) => x.version === v.version - 1);
      const target = prev ?? versions.find((x) => x.id !== v.id);
      if (!target) {
        setError("没有可对比的版本");
        return;
      }
      const res = await api.compareWorkbenchVersions(campaignId, rowId, target.id, v.id);
      const keys = Object.keys(res.diff || {});
      const summary = keys.length
        ? keys.join(", ")
        : "两个版本内容一致";
      setDiff({ v1: target.version, v2: v.version, summary });
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-edge rounded-lg shadow-xl w-[32rem] max-w-[92vw] p-4 text-sm"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-slate-200">
            🕘 版本历史（行 #{rowId}）
            {refcode && (
              <span className="ml-2 text-xs text-slate-500 font-normal">{refcode}</span>
            )}
          </h3>
          <button
            className="text-slate-400 hover:text-slate-200"
            onClick={onClose}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        <p className="text-xs text-slate-500 mb-3">
          每次保存台账时 DataLab 自动留档（零成本审计轨迹）
        </p>

        {error && (
          <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2 mb-2 text-xs">
            {error}
          </div>
        )}

        {versions.length === 0 ? (
          <p className="text-slate-500 text-xs">暂无版本记录</p>
        ) : (
          <>
            <ul className="space-y-1.5 max-h-64 overflow-y-auto">
              {versions.map((v) => (
                <li
                  key={v.id}
                  className="flex items-center gap-2 text-xs border border-edge/50 rounded px-2 py-1.5 bg-ink/20"
                >
                  <span className="font-semibold text-slate-300">v{v.version}</span>
                  <span className="text-slate-400">{v.action}</span>
                  <span className="flex-1 truncate text-slate-500">
                    {v.timestamp ? v.timestamp.replace("T", " ") : ""}
                    {v.creator ? ` · ${v.creator}` : ""}
                  </span>
                  <button
                    className="text-accent hover:text-accent/80 disabled:opacity-40"
                    disabled={busy || versions.length < 2}
                    onClick={() => compare(v)}
                    title="与上一版本对比"
                  >
                    ⚖ 对比
                  </button>
                  <button
                    className="text-red-400 hover:text-red-300 disabled:opacity-40"
                    disabled={restoring != null}
                    onClick={() => restore(v)}
                    title="恢复到该版本（可逆，平台留新版本）"
                  >
                    {restoring === v.id ? "恢复中…" : "⏪ 恢复"}
                  </button>
                </li>
              ))}
            </ul>
            {diff && (
              <div className="mt-2 text-xs border border-edge/50 rounded p-2 bg-ink/20 text-slate-300">
                <span className="text-slate-400">
                  v{diff.v1} → v{diff.v2} 差异：
                </span>{" "}
                {diff.summary}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
