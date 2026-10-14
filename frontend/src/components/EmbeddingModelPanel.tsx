import { useCallback, useEffect, useRef, useState } from "react";
import { api, type EmbeddingModel } from "../api";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function EmbeddingModelPanel() {
  const [models, setModels] = useState<EmbeddingModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [dlProgress, setDlProgress] = useState(0);
  const [switching, setSwitching] = useState(false);
  const [swProgress, setSwProgress] = useState(0);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // Poll timers must not outlive the panel (closing Settings mid-download used to leave them running).
  const timers = useRef<Set<ReturnType<typeof setInterval>>>(new Set());

  const load = useCallback(async () => {
    try {
      setModels(await api.embeddingModels());
    } catch (e) {
      setMsg({ ok: false, text: `模型列表加载失败: ${errText(e)}` });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const active = timers.current;
    return () => {
      active.forEach(clearInterval);
      active.clear();
    };
  }, [load]);

  /** Poll `tick` every `ms` until it returns true (finished) or throws (the error is shown, polling stops). */
  const poll = (ms: number, tick: () => Promise<boolean>, onError: (e: unknown) => void) => {
    const iv = setInterval(async () => {
      try {
        if (await tick()) {
          clearInterval(iv);
          timers.current.delete(iv);
        }
      } catch (e) {
        clearInterval(iv);
        timers.current.delete(iv);
        onError(e);
      }
    }, ms);
    timers.current.add(iv);
  };

  const pollDownload = (taskId: string, modelId: string) =>
    poll(
      1000,
      async () => {
        const t = await api.embeddingDownloadTask(taskId);
        setDlProgress(t.progress);
        if (t.status === "done") {
          setDownloading(null);
          setMsg({ ok: true, text: `下载完成: ${modelId}` });
          void load();
          return true;
        }
        if (t.status === "failed") {
          setDownloading(null);
          setMsg({ ok: false, text: `下载失败: ${t.error || "未知错误"}` });
          return true;
        }
        return false;
      },
      (e) => {
        setDownloading(null);
        setMsg({ ok: false, text: `下载进度查询失败: ${errText(e)}` });
      },
    );

  const onDownload = async (m: EmbeddingModel) => {
    // 大模型二次确认（bge-m3 ~2GB）
    const big = m.size_estimate.includes("GB");
    if (big && !window.confirm(`下载 ${m.label}（约 ${m.size_estimate}），确定吗？`)) {
      return;
    }
    setDownloading(m.id);
    setDlProgress(0);
    setMsg(null);
    try {
      const { task_id } = await api.downloadEmbeddingModel(m.id);
      pollDownload(task_id, m.id);
    } catch (e) {
      setDownloading(null);
      setMsg({ ok: false, text: `下载启动失败: ${errText(e)}` });
    }
  };

  const pollSwitch = () =>
    poll(
      2000,
      async () => {
        const s = await api.embeddingSwitchStatus();
        setSwProgress(s.progress);
        if (s.status === "done") {
          setSwitching(false);
          setMsg({ ok: true, text: "模型切换完成，索引已重建" });
          void load();
          return true;
        }
        if (s.status === "failed") {
          setSwitching(false);
          setMsg({ ok: false, text: `切换失败: ${s.error || "未知错误"}` });
          return true;
        }
        return false;
      },
      (e) => {
        setSwitching(false);
        setMsg({ ok: false, text: `切换进度查询失败: ${errText(e)}` });
      },
    );

  const onSwitch = async (m: EmbeddingModel) => {
    if (
      !window.confirm(
        `切换到 ${m.label}？将重建全库索引（约 10-20 分钟），期间检索不可用。`
      )
    ) {
      return;
    }
    setSwitching(true);
    setSwProgress(0);
    setMsg(null);
    try {
      await api.switchEmbeddingModel(m.id);
      pollSwitch();
    } catch (e) {
      setSwitching(false);
      setMsg({ ok: false, text: `切换启动失败: ${errText(e)}` });
    }
  };

  if (loading) return <div className="text-sm text-slate-400">加载模型列表…</div>;

  return (
    <div className="border-t border-edge pt-4 mt-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-1">Embedding 模型</h3>
      <p className="text-xs text-slate-400 mb-3">
        选择向量模型。切换模型会重建全库索引（维度不同）。多语言模型（bge-m3）可解决跨语言召回问题。
      </p>
      {msg && (
        <div
          className={`text-xs rounded px-3 py-2 border mb-3 ${
            msg.ok
              ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
              : "border-rose-500/40 text-rose-400 bg-rose-500/10"
          }`}
        >
          {msg.ok ? "✓ " : "✗ "}
          {msg.text}
        </div>
      )}
      <div className="space-y-2">
        {models.map((m) => (
          <div
            key={m.id}
            className={`border rounded px-3 py-2 flex items-center justify-between ${
              m.current ? "border-accent/60 bg-accent/5" : "border-edge"
            }`}
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm text-slate-200 flex items-center gap-2">
                {m.label}
                {m.current && (
                  <span className="text-[10px] bg-accent/20 text-accent rounded px-1.5 py-0.5">
                    当前使用
                  </span>
                )}
                {m.langs === "multi" && (
                  <span className="text-[10px] bg-blue-500/20 text-blue-400 rounded px-1.5 py-0.5">
                    多语言推荐
                  </span>
                )}
              </div>
              <div className="text-xs text-slate-400 truncate">
                {m.dim}d · {m.size_estimate} · {m.note}
              </div>
              {downloading === m.id && (
                <div className="text-xs text-slate-400 mt-1">
                  下载中… {dlProgress.toFixed(1)}%
                </div>
              )}
            </div>
            <div className="ml-3 flex-shrink-0">
              {m.current ? null : m.cached ? (
                <button
                  onClick={() => void onSwitch(m)}
                  disabled={switching || downloading !== null}
                  className="text-xs bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-1.5 disabled:opacity-40"
                >
                  {switching ? `${swProgress.toFixed(0)}%` : "切换"}
                </button>
              ) : (
                <button
                  onClick={() => void onDownload(m)}
                  disabled={switching || downloading !== null}
                  className="text-xs border border-edge text-slate-300 rounded px-3 py-1.5 hover:border-accent/40 hover:text-accent disabled:opacity-40"
                >
                  {downloading === m.id ? `${dlProgress.toFixed(0)}%` : "下载"}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {switching && (
        <div className="mt-3 text-xs text-amber-400">
          ⚠ 索引重建中（{swProgress.toFixed(1)}%），期间检索不可用，请勿关闭页面。
        </div>
      )}
    </div>
  );
}
