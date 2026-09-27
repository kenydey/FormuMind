/**
 * W3-9: Session Plan 审批 —— 计划详情 + pending 状态下的 批准/拒绝 按钮。
 * 审批不可逆：点击后二次确认，确认后调 POST /api/session-plans/{id}/decide。
 */
import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import { api, formatApiError, type SessionPlan } from "../api";

const STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: "待审批", cls: "text-amber-300 border-amber-500/40 bg-amber-500/10" },
  approved: { label: "已批准", cls: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10" },
  rejected: { label: "已拒绝", cls: "text-rose-300 border-rose-500/40 bg-rose-500/10" },
};

const STEP_ICON: Record<string, string> = {
  done: "✓",
  in_progress: "◐",
  failed: "✕",
  pending: "○",
};

export default function SessionPlanModal({
  planId,
  onClose,
}: {
  planId: string;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<SessionPlan | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** null = no confirm dialog; otherwise the pending decision awaiting confirmation */
  const [confirming, setConfirming] = useState<boolean | null>(null);
  const [deciding, setDeciding] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await api.getSessionPlan(planId);
      setPlan(p);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }, [planId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(approved: boolean) {
    setDeciding(true);
    setError(null);
    try {
      const p = await api.decideSessionPlan(planId, approved);
      setPlan(p);
    } catch (e) {
      // e.g. 409 = already decided: surface the error and reload authoritative state
      setError(formatApiError(e));
      await load();
    } finally {
      setDeciding(false);
      setConfirming(null);
    }
  }

  const statusMeta = STATUS_META[plan?.status ?? ""] ?? STATUS_META.pending;
  const isPending = plan?.status === "pending";

  return (
    <Modal
      title="📋 会话计划审批"
      open
      onClose={onClose}
      size="lg"
      testId="session-plan-modal"
    >
      <div className="space-y-3 text-sm">
        {error && (
          <div className="text-xs text-rose-400 bg-rose-500/10 rounded p-2">{error}</div>
        )}
        {busy ? (
          <div className="text-xs text-slate-500 py-6 text-center">计划加载中…</div>
        ) : !plan ? (
          <div className="text-xs text-slate-500 py-6 text-center">未找到该计划</div>
        ) : (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={`text-[11px] px-2 py-0.5 rounded border ${statusMeta.cls}`}
                data-testid="plan-status"
              >
                {statusMeta.label}
              </span>
              <span className="text-[11px] font-mono text-slate-500">{plan.plan_id}</span>
              {plan.decided_at && (
                <span className="text-[11px] text-slate-500">
                  决策于 {plan.decided_at}
                  {plan.decided_by ? ` · ${plan.decided_by}` : ""}
                </span>
              )}
            </div>

            <div className="space-y-2 max-h-[50vh] overflow-auto pr-1">
              {plan.phases.map((ph, i) => (
                <div key={`${ph.name}-${i}`} className="border border-edge rounded p-2 bg-ink/20">
                  <div className="text-xs font-medium text-slate-200 mb-1">
                    阶段 {i + 1} · {ph.name}
                  </div>
                  <ul className="space-y-0.5">
                    {ph.steps.map((s, j) => (
                      <li key={j} className="flex items-start gap-1.5 text-[11px] text-slate-300">
                        <span className="text-slate-500 mt-px">
                          {STEP_ICON[s.status] ?? "○"}
                        </span>
                        <span>{s.desc}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>

            {isPending && confirming === null && (
              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setConfirming(true)}
                  className="flex-1 text-xs border border-emerald-500/50 text-emerald-300 rounded px-3 py-1.5 hover:bg-emerald-500/10"
                  data-testid="plan-approve-btn"
                >
                  ✓ 批准执行
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  className="flex-1 text-xs border border-rose-500/50 text-rose-300 rounded px-3 py-1.5 hover:bg-rose-500/10"
                  data-testid="plan-reject-btn"
                >
                  ✕ 拒绝
                </button>
              </div>
            )}

            {isPending && confirming !== null && (
              <div
                className="border border-amber-500/40 bg-amber-500/5 rounded p-3"
                data-testid="plan-confirm-box"
              >
                <p className="text-xs text-amber-200 mb-2">
                  确认{confirming ? "批准" : "拒绝"}该计划？此操作不可逆。
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => void decide(confirming)}
                    className={`flex-1 text-xs rounded px-3 py-1.5 border disabled:opacity-50 ${
                      confirming
                        ? "border-emerald-500/50 text-emerald-300 hover:bg-emerald-500/10"
                        : "border-rose-500/50 text-rose-300 hover:bg-rose-500/10"
                    }`}
                    data-testid="plan-confirm-btn"
                  >
                    {deciding ? "提交中…" : `确认${confirming ? "批准" : "拒绝"}`}
                  </button>
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => setConfirming(null)}
                    className="flex-1 text-xs border border-edge text-slate-400 rounded px-3 py-1.5 hover:text-slate-200 disabled:opacity-50"
                    data-testid="plan-cancel-btn"
                  >
                    取消
                  </button>
                </div>
              </div>
            )}

            {!isPending && (
              <p className="text-[11px] text-slate-500">
                该计划已{plan.status === "approved" ? "批准" : "拒绝"}，审批不可逆。
              </p>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
