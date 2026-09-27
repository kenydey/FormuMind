/**
 * W5-4 (P1-28): Reviewer 审计页（Modal 形态）—— review runs 列表 + 单 run
 * 详情：action log（claim 级 disposition 时间线）、stale 提示、手动重审。
 *
 * 从 ReviewerCard 的"审计详情"打开（带 initialRunId + 问答上下文可重审）；
 * 无 initialRunId 时为纯列表浏览，此时重审按钮禁用（缺问答输入）。
 */
import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import {
  reviewsApi,
  formatApiError,
  type ReviewRunDetail,
  type ReviewRunSummary,
} from "../api";

const DISPOSITION_CLS: Record<string, string> = {
  resolved: "text-emerald-300",
  open: "text-amber-300",
  unaddressed: "text-rose-300",
};

function StaleNote({ run }: { run: ReviewRunSummary }) {
  if (run.stale === true) {
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-500/10 border border-rose-500/40 text-rose-300">
        已过期{run.stale_reason ? `：${run.stale_reason}` : ""}
      </span>
    );
  }
  if (run.stale === "unverified") {
    return (
      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/40 text-amber-300">
        未验证
      </span>
    );
  }
  return null;
}

export default function ReviewerAuditModal({
  open,
  onClose,
  initialRunId,
  projectId,
  question,
  answer,
  citations,
}: {
  open: boolean;
  onClose: () => void;
  initialRunId?: string | null;
  projectId?: string | null;
  question?: string;
  answer?: string;
  citations?: unknown[];
}) {
  const [runs, setRuns] = useState<ReviewRunSummary[]>([]);
  const [detail, setDetail] = useState<ReviewRunDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(initialRunId ?? null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);

  const loadList = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await reviewsApi.listReviewRuns({
        projectId: projectId ?? undefined,
        limit: 30,
      });
      setRuns(res.items ?? []);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId]);

  const loadDetail = useCallback(async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      setDetail(await reviewsApi.getReviewRun(id));
      setSelectedId(id);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    if (initialRunId) {
      void loadDetail(initialRunId);
      void loadList();
    } else {
      setDetail(null);
      setSelectedId(null);
      void loadList();
    }
  }, [open, initialRunId, loadDetail, loadList]);

  async function rerun() {
    if (!selectedId || !question || !answer) return;
    setRerunning(true);
    setError(null);
    try {
      const res = await reviewsApi.rerunReviewRun(selectedId, {
        question,
        answer,
        citations: citations ?? [],
        project_id: projectId ?? detail?.project_id ?? null,
      });
      const newRunId = (res.fix as { run_id?: string } | null)?.run_id;
      await loadList();
      if (newRunId) await loadDetail(newRunId);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setRerunning(false);
    }
  }

  const dispositions = detail?.dispositions ?? {};
  const dispEntries = Object.entries(dispositions);

  return (
    <Modal
      title="证据审计"
      open={open}
      onClose={onClose}
      size="lg"
      testId="reviewer-audit-modal"
    >
      <div className="grid grid-cols-12 gap-3 min-h-[320px]">
        <div className="col-span-4 border-r border-edge pr-3 max-h-[420px] overflow-y-auto">
          <div className="text-[11px] text-slate-500 mb-2">审计记录（最新在前）</div>
          {runs.length === 0 && !busy && (
            <div className="text-[11px] text-slate-500">暂无审计记录。</div>
          )}
          {runs.map((r) => (
            <button
              key={r.run_id}
              data-testid="reviewer-audit-row"
              onClick={() => void loadDetail(r.run_id)}
              className={`w-full text-left px-2 py-1.5 rounded mb-1 border text-[11px] ${
                selectedId === r.run_id
                  ? "border-accent/50 bg-accent/10"
                  : "border-edge hover:border-accent/30"
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-slate-300 truncate">
                  {r.run_id.slice(0, 24)}…
                </span>
                <StaleNote run={r} />
              </div>
              <div className="text-slate-500 mt-0.5">
                {r.outcome ?? "null"} · ⚠{r.warn_count ?? 0} ✕{r.fail_count ?? 0}
              </div>
            </button>
          ))}
        </div>
        <div className="col-span-8 max-h-[420px] overflow-y-auto">
          {busy && !detail ? (
            <div className="text-[11px] text-slate-500">加载中…</div>
          ) : detail ? (
            <div>
              <div className="flex items-center gap-2 flex-wrap mb-2">
                <span className="font-mono text-[11px] text-slate-300">
                  {detail.run_id}
                </span>
                <span className="text-[11px] text-slate-500">
                  {detail.status} / {detail.outcome}
                </span>
                <StaleNote run={detail} />
                <span className="ml-auto">
                  <button
                    onClick={() => void rerun()}
                    disabled={rerunning || !question || !answer}
                    className="text-[11px] px-2 py-0.5 rounded border border-edge text-slate-300 hover:border-accent/50 hover:text-accent disabled:opacity-40"
                    title={
                      question && answer
                        ? "对该问答重新跑一遍证据审计"
                        : "需从会话消息打开才能重审（缺问答输入）"
                    }
                  >
                    {rerunning ? "重审中…" : "重审"}
                  </button>
                </span>
              </div>
              {detail.stale === true && (
                <div className="mb-2 text-[11px] px-2 py-1 rounded bg-rose-500/10 border border-rose-500/40 text-rose-300">
                  ⚠️ 结论已过期：{detail.stale_reason}——原结论不可信。
                </div>
              )}
              <div className="text-[11px] text-slate-500 mb-1">
                Action log（claim 级处置时间线）
              </div>
              {dispEntries.length === 0 ? (
                <div className="text-[11px] text-slate-500">无 claim 记录。</div>
              ) : (
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-slate-500 border-b border-edge">
                      <th className="py-1 pr-2">断言</th>
                      <th className="py-1 pr-2">状态</th>
                      <th className="py-1 pr-2">处置</th>
                      <th className="py-1">重标次</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dispEntries.map(([note, d]) => (
                      <tr key={note} className="border-b border-edge/40">
                        <td className="py-1 pr-2 text-slate-300 max-w-[280px] truncate" title={note}>
                          {note}
                        </td>
                        <td className="py-1 pr-2 text-slate-400">{d.status ?? "—"}</td>
                        <td className={`py-1 pr-2 ${DISPOSITION_CLS[String(d.disposition)] ?? "text-slate-400"}`}>
                          {d.disposition ?? "—"}
                        </td>
                        <td className="py-1 text-slate-400">{d.reflag_count ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ) : (
            <div className="text-[11px] text-slate-500">
              左侧选择一条审计记录查看详情。
            </div>
          )}
          {error && (
            <div className="mt-2 text-[11px] text-rose-300/80">{error}</div>
          )}
        </div>
      </div>
    </Modal>
  );
}
