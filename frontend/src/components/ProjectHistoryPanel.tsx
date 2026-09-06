import { useEffect, useState } from "react";
import {
  api,
  formatApiError,
  type ProjectPayloadVersion,
} from "../api";
import { useStore } from "../store";

/**
 * Payload version history + rollback for the active project.
 * Mounted inside HistoryPanel when a project is active.
 */
export default function ProjectHistoryPanel({ projectId }: { projectId: string }) {
  const loadProject = useStore((s) => s.loadProject);
  const [versions, setVersions] = useState<ProjectPayloadVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyVersion, setBusyVersion] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<ProjectPayloadVersion | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getProjectHistory(projectId, 30);
      setVersions(res.versions ?? []);
    } catch (e) {
      setVersions([]);
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function doRollback(version: number) {
    setBusyVersion(version);
    setError(null);
    try {
      await api.rollbackProject(projectId, version);
      setConfirm(null);
      await loadProject(projectId);
      await refresh();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusyVersion(null);
    }
  }

  return (
    <div className="border border-edge/50 rounded-lg p-2.5 space-y-2" data-testid="project-history">
      <div className="flex items-center justify-between">
        <h3 className="text-[10px] uppercase tracking-widest text-accent2">Payload 版本历史</h3>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="text-[10px] text-slate-500 hover:text-accent disabled:opacity-40"
        >
          刷新
        </button>
      </div>

      {error && <p className="text-[10px] text-rose-400">{error}</p>}
      {loading && versions.length === 0 && (
        <p className="text-[10px] text-slate-500">加载中…</p>
      )}
      {!loading && versions.length === 0 && !error && (
        <p className="text-[10px] text-slate-500">尚无版本快照（保存项目后会出现）</p>
      )}

      <ul className="max-h-48 overflow-y-auto space-y-1">
        {versions.map((v) => (
          <li
            key={v.version}
            className="flex items-start justify-between gap-2 text-[10px] border border-edge/40 rounded px-2 py-1.5 bg-ink/40"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-accent">v{v.version}</span>
                <span className="text-slate-500 truncate">{v.cause || "—"}</span>
              </div>
              <div className="text-slate-600 mt-0.5">
                {v.created_at?.slice(0, 19).replace("T", " ") ?? "—"} · 对话{" "}
                {v.chat_count} · 资料 {v.source_count}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setConfirm(v)}
              disabled={busyVersion != null}
              className="shrink-0 text-amber-300/90 border border-amber-500/30 rounded px-1.5 py-0.5 hover:bg-amber-500/10 disabled:opacity-40"
            >
              回滚
            </button>
          </li>
        ))}
      </ul>

      {confirm && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60">
          <div className="bg-panel border border-edge rounded-lg shadow-xl w-80 max-w-[92vw] p-4 text-sm">
            <h4 className="font-semibold text-slate-200 mb-2">确认回滚到 v{confirm.version}？</h4>
            <p className="text-xs text-slate-400 mb-3">
              当前 payload 会先快照，再恢复该版本。原因：{confirm.cause || "—"}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busyVersion != null}
                onClick={() => void doRollback(confirm.version)}
                className="flex-1 bg-amber-500/90 text-ink font-semibold rounded px-3 py-1.5 disabled:opacity-50"
              >
                {busyVersion === confirm.version ? "回滚中…" : "确认回滚"}
              </button>
              <button
                type="button"
                disabled={busyVersion != null}
                onClick={() => setConfirm(null)}
                className="flex-1 border border-edge text-slate-400 rounded px-3 py-1.5"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
