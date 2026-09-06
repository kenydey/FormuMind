import { useEffect, useState } from "react";
import { api, type PlatformHealth } from "../api";

/**
 * Surfaces /health degraded signals (Datalab ELN, Redis broker, DB, PDF parser)
 * that DegradedBanner does not cover (LLM key / online deps).
 */
export default function InfraHealthBanner() {
  const [health, setHealth] = useState<PlatformHealth | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    async function poll() {
      try {
        const h = await api.getHealth();
        if (!cancelled) setHealth(h);
      } catch {
        if (!cancelled) {
          setHealth({
            status: "degraded",
            database: { ok: false, scheme: "unknown" },
            task_broker: { required: true, reachable: false },
            parsers: {},
            datalab: { required: false, reachable: false },
          });
        }
      }
    }

    void poll();
    timer = setInterval(() => void poll(), 30_000);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, []);

  if (dismissed || !health || health.status === "ok") return null;

  const reasons: string[] = [];
  if (!health.database?.ok) reasons.push("数据库不可用");
  if (health.datalab?.required && !health.datalab?.reachable) {
    reasons.push(health.datalab.hint ? `Datalab ELN：${health.datalab.hint}` : "Datalab ELN 不可达");
  }
  if (health.task_broker?.required && !health.task_broker?.reachable) {
    reasons.push("任务队列（Redis/Celery）不可达");
  }
  if (health.parsers && health.parsers.pdf === false) {
    reasons.push("PDF 解析器未就绪");
  }
  if (reasons.length === 0) reasons.push("平台健康检查返回 degraded");

  return (
    <div
      className="shrink-0 px-5 py-2 bg-rose-500/10 border-b border-rose-500/30 flex items-center gap-3 text-xs text-rose-300"
      data-testid="infra-health-banner"
      role="status"
    >
      <span className="font-semibold">⚠ 基础设施降级</span>
      <span className="text-rose-200/80">{reasons.join(" · ")}</span>
      <button
        type="button"
        onClick={() => void api.getHealth().then(setHealth).catch(() => undefined)}
        className="ml-auto shrink-0 border border-rose-400/40 text-rose-200 rounded px-2.5 py-1 hover:bg-rose-400/15"
      >
        重新检测
      </button>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="shrink-0 text-rose-200/60 hover:text-rose-100"
        title="忽略"
      >
        ✕
      </button>
    </div>
  );
}
