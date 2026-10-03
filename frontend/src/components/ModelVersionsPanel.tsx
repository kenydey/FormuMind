import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError, type ModelInfo, type ModelVersionMeta } from "../api";

function fmtTime(iso?: string | null, versionId?: string): string {
  const raw = iso || versionId || "";
  const d = iso ? new Date(iso) : null;
  if (d && !Number.isNaN(d.getTime())) return d.toLocaleString("zh-CN", { hour12: false });
  return raw.slice(0, 19).replace("T", " ") || "—";
}

type Props = {
  model: ModelInfo;
  /** Called after a rollback / release so the host can refetch the served models. */
  onChanged?: () => void | Promise<void>;
};

/**
 * 模型版本抽屉 —— 列出某个指标的历史版本，回滚（锁定）或解除锁定。
 *
 * 回滚会把所选版本锁定为在线服务版本：之后的重训只会存档新版本，不会覆盖它；
 * 「解除锁定」切到最新存档版本并恢复自动跟随。此前后端已有 versions / rollback
 * 接口，但界面从未调用，回滚也会被下一次重训悄悄覆盖。
 */
export default function ModelVersionsPanel({ model, onChanged }: Props) {
  const projectId = model.project_id ?? "";
  const [versions, setVersions] = useState<ModelVersionMeta[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!projectId) return;
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const rows = await api.modelVersions(projectId, model.metric);
      if (mine === seq.current) setVersions(rows);
    } catch (e) {
      if (mine === seq.current) {
        setVersions(null);
        setError(formatApiError(e));
      }
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [projectId, model.metric]);

  // Refetch when the served version changes underneath (rollback elsewhere, a retrain).
  useEffect(() => {
    void load();
  }, [load, model.version_id, model.pinned, model.newer_version_id]);

  async function act(label: string, fn: () => Promise<unknown>) {
    if (busy) return;
    setBusy(label);
    setError(null);
    try {
      await fn();
      setConfirm(null);
      await onChanged?.();
      await load();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  }

  if (!projectId) {
    return (
      <p className="mt-1 text-[10px] text-slate-500" data-testid="model-versions-unavailable">
        该模型没有项目归属，无法管理版本。
      </p>
    );
  }

  return (
    <div className="mt-1.5 space-y-1 border-t border-edge/40 pt-1.5 text-[10px]" data-testid={`model-versions-${model.metric}`}>
      {model.pinned && (
        <div className="flex flex-wrap items-center gap-1.5 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-1 text-amber-200">
          <span data-testid="model-pinned-note">
            🔒 已锁定在回滚版本
            {model.newer_version_id ? "；重训产生的新版本已存档，未生效" : ""}
          </span>
          <button
            type="button"
            disabled={busy !== null}
            className="ml-auto rounded border border-amber-400/50 px-1.5 py-0.5 hover:bg-amber-500/20 disabled:opacity-50"
            onClick={() => void act("unpin", () => api.unpinModel(projectId, model.metric))}
            data-testid="model-unpin"
          >
            {busy === "unpin" ? "切换中…" : "解除锁定并使用最新"}
          </button>
        </div>
      )}

      {error && (
        <p className="text-rose-400" data-testid="model-versions-error">
          {error}
        </p>
      )}
      {loading && !versions && <p className="text-slate-500">加载版本…</p>}
      {versions && versions.length === 0 && <p className="text-slate-500">尚无存档版本。</p>}

      <ul className="max-h-40 space-y-1 overflow-y-auto">
        {(versions ?? []).map((v, idx) => {
          const isNewest = idx === 0;
          return (
            <li
              key={v.version_id}
              className={`rounded border px-1.5 py-1 ${v.is_current ? "border-accent/50 bg-accent/5" : "border-edge/40"}`}
              data-testid={`model-version-${v.version_id}`}
            >
              <div className="flex flex-wrap items-center gap-1">
                <span className="font-mono text-slate-300">{fmtTime(v.trained_at, v.version_id)}</span>
                {v.is_current && <span className="rounded border border-accent/40 px-1 text-accent">当前</span>}
                {v.is_current && v.pinned && <span className="rounded border border-amber-500/40 px-1 text-amber-300">锁定</span>}
                {isNewest && !v.is_current && <span className="rounded border border-emerald-500/40 px-1 text-emerald-300">最新</span>}
              </div>
              <div className="text-slate-500">
                {v.backend ?? "?"}
                {v.n_samples != null ? ` · n=${v.n_samples}` : ""}
                {v.r2 != null ? ` · R²=${Number(v.r2).toFixed(2)}` : ""}
                {v.rmse != null ? ` · RMSE=${Number(v.rmse).toFixed(Number(v.rmse) < 1 ? 3 : 1)}` : ""}
              </div>
              {!v.is_current && (
                <div className="mt-0.5">
                  {confirm === v.version_id ? (
                    <span className="flex flex-wrap items-center gap-1">
                      <span className="text-amber-300">回滚后将锁定该版本，重训不会覆盖。</span>
                      <button
                        type="button"
                        disabled={busy !== null}
                        className="rounded border border-amber-400/50 px-1.5 py-0.5 text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
                        onClick={() =>
                          void act("rollback", () => api.rollbackModel(projectId, model.metric, v.version_id))
                        }
                        data-testid={`model-rollback-confirm-${v.version_id}`}
                      >
                        {busy === "rollback" ? "回滚中…" : "确认回滚"}
                      </button>
                      <button
                        type="button"
                        className="rounded border border-edge px-1.5 py-0.5 text-slate-400"
                        onClick={() => setConfirm(null)}
                      >
                        取消
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      disabled={busy !== null}
                      className="rounded border border-edge px-1.5 py-0.5 text-slate-300 hover:border-accent/40 disabled:opacity-50"
                      onClick={() => setConfirm(v.version_id)}
                      data-testid={`model-rollback-${v.version_id}`}
                    >
                      回滚到此版本
                    </button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
