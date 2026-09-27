/**
 * W5-4 (P1-28): Reviewer 审计卡片 —— 会话内嵌，展示某条 assistant 回答的
 * review run：warn/fail 计数、stale 提示、手动重审、审计详情入口。
 *
 * runId 来自 SSE done 事件的 reviewer_fix.run_id（searchSlice 挂到消息上）。
 * question/answer 由调用方（ResearchPanel 消息流）传入，供重审使用。
 */
import { useCallback, useEffect, useState } from "react";
import { reviewsApi, formatApiError, type ReviewRunDetail } from "../api";
import ReviewerAuditModal from "./ReviewerAuditModal";

const OUTCOME_META: Record<string, { label: string; cls: string }> = {
  pass: { label: "通过", cls: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10" },
  flagged: { label: "有发现", cls: "text-amber-300 border-amber-500/40 bg-amber-500/10" },
  null: { label: "未结论", cls: "text-slate-400 border-edge bg-ink/40" },
};

function StaleBanner({ stale, reason }: { stale: boolean | "unverified" | null | undefined; reason?: string | null }) {
  if (stale === true) {
    return (
      <div
        data-testid="reviewer-card-stale"
        className="mt-1.5 text-[11px] px-2 py-1 rounded bg-rose-500/10 border border-rose-500/40 text-rose-300"
      >
        ⚠️ 审计结论已过期{reason ? `：${reason}` : ""}——原结论不可信，可重审。
      </div>
    );
  }
  if (stale === "unverified") {
    return (
      <div
        data-testid="reviewer-card-stale"
        className="mt-1.5 text-[11px] px-2 py-1 rounded bg-amber-500/10 border border-amber-500/40 text-amber-300"
      >
        新鲜度未验证{reason ? `：${reason}` : ""}。
      </div>
    );
  }
  return null;
}

export default function ReviewerCard({
  runId,
  question,
  answer,
  citations,
}: {
  runId: string;
  question: string;
  answer: string;
  citations?: unknown[];
}) {
  const [run, setRun] = useState<ReviewRunDetail | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [activeRunId, setActiveRunId] = useState(runId);

  const load = useCallback(async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      setRun(await reviewsApi.getReviewRun(id));
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    setActiveRunId(runId);
    void load(runId);
  }, [runId, load]);

  async function rerun() {
    setRerunning(true);
    setError(null);
    try {
      const res = await reviewsApi.rerunReviewRun(activeRunId, {
        question,
        answer,
        citations: citations ?? [],
        project_id: run?.project_id ?? null,
      });
      const newRunId = (res.fix as { run_id?: string } | null)?.run_id;
      if (newRunId) {
        setActiveRunId(newRunId);
        await load(newRunId);
      } else {
        await load(activeRunId);
      }
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setRerunning(false);
    }
  }

  if (busy) {
    return (
      <div data-testid="reviewer-card" className="mt-2 text-[11px] text-slate-500">
        审计加载中…
      </div>
    );
  }
  if (error && !run) {
    return (
      <div data-testid="reviewer-card" className="mt-2 text-[11px] text-slate-500">
        审计记录不可用：{error}
      </div>
    );
  }
  const outcome = OUTCOME_META[String(run?.outcome ?? "null")] ?? OUTCOME_META.null;
  return (
    <div
      data-testid="reviewer-card"
      className="mt-2 pt-2 border-t border-edge/60"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] text-slate-500">证据审计</span>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded border ${outcome.cls}`}
        >
          {outcome.label}
        </span>
        <span data-testid="reviewer-card-warn" className="text-[10px] text-amber-300/90">
          ⚠ {run?.warn_count ?? 0}
        </span>
        <span data-testid="reviewer-card-fail" className="text-[10px] text-rose-300/90">
          ✕ {run?.fail_count ?? 0}
        </span>
        {(run?.unaddressed_count ?? 0) > 0 && (
          <span className="text-[10px] text-slate-400">
            未处置 {run?.unaddressed_count}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          <button
            data-testid="reviewer-card-rerun"
            onClick={() => void rerun()}
            disabled={rerunning}
            className="text-[11px] px-2 py-0.5 rounded border border-edge text-slate-300 hover:border-accent/50 hover:text-accent disabled:opacity-50"
            title="对当前问答重新跑一遍证据审计"
          >
            {rerunning ? "重审中…" : "重审"}
          </button>
          <button
            data-testid="reviewer-card-audit"
            onClick={() => setAuditOpen(true)}
            className="text-[11px] px-2 py-0.5 rounded border border-edge text-slate-300 hover:border-accent/50 hover:text-accent"
            title="打开审计详情（action log）"
          >
            审计详情
          </button>
        </span>
      </div>
      <StaleBanner stale={run?.stale} reason={run?.stale_reason} />
      {error && (
        <div className="mt-1 text-[11px] text-rose-300/80">重审失败：{error}</div>
      )}
      {auditOpen && (
        <ReviewerAuditModal
          open={auditOpen}
          onClose={() => setAuditOpen(false)}
          initialRunId={activeRunId}
          question={question}
          answer={answer}
          citations={citations}
        />
      )}
    </div>
  );
}
