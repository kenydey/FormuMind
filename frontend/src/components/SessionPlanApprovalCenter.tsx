/**
 * W3-9: Session Plan 审批中心 —— 轮询后端待审批计划，有 pending 时弹出
 * SessionPlanModal（批准/拒绝走已有 /api/session-plans/{id}/decide）。
 * 挂载到 App 根节点。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import SessionPlanModal from "./SessionPlanModal";
import { api } from "../api";

const POLL_MS = 5000;

export default function SessionPlanApprovalCenter() {
  const [planId, setPlanId] = useState<string | null>(null);
  /** 用户手动关闭但未决策的计划：本轮 pending 期间不再打扰 */
  const dismissedRef = useRef<Set<string>>(new Set());

  const poll = useCallback(async () => {
    let ids: Set<string>;
    let first: string | null;
    try {
      const res = await api.listPendingSessionPlans();
      const items = res.items ?? [];
      ids = new Set(items.map((i) => i.plan_id));
      first = items.find((i) => !dismissedRef.current.has(i.plan_id))?.plan_id ?? null;
    } catch {
      return; // 轮询失败保持现状，下次再试
    }
    for (const id of [...dismissedRef.current]) {
      if (!ids.has(id)) dismissedRef.current.delete(id);
    }
    setPlanId((prev) => {
      if (prev && ids.has(prev) && !dismissedRef.current.has(prev)) return prev;
      return first;
    });
  }, []);

  useEffect(() => {
    void poll();
    const t = setInterval(() => {
      void poll();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [poll]);

  if (!planId) return null;
  return (
    <SessionPlanModal
      planId={planId}
      onClose={() => {
        dismissedRef.current.add(planId);
        setPlanId(null);
      }}
    />
  );
}
