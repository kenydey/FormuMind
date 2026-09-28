/**
 * W3-11: MCP 审批中心 —— 轮询后端 pending 审批，有待审批时弹出 McpApprovalDialog，
 * 决策直接调用后端 REST 接口（GET /api/mcp/approvals/pending，
 * POST /api/mcp/approvals/{id}/decide）。挂载到 App 根节点。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import McpApprovalDialog, { type McpApprovalRequest } from "./McpApprovalDialog";
import { api, type McpApprovalPendingItem } from "../api";

const POLL_MS = 3000;

function toRequest(item: McpApprovalPendingItem): McpApprovalRequest {
  return {
    id: item.request_id,
    server_id: item.server_id,
    tool_name: item.tool_name,
    session_id: item.session_id ?? null,
    project_id: item.project_id ?? null,
    // F-8: pending 接口不返回 arguments（后端未持久化）；requested_at 删去
    // （之前由 age 反推计算但从未被展示）。
    arguments: null,
  };
}

export default function McpApprovalCenter() {
  const [current, setCurrent] = useState<McpApprovalRequest | null>(null);
  /** 用户手动关闭但未决策的请求 id：本轮 pending 期间不再打扰 */
  const dismissedRef = useRef<Set<number>>(new Set());

  const poll = useCallback(async () => {
    let items: McpApprovalPendingItem[];
    try {
      const res = await api.getMcpApprovalPending();
      items = res.items ?? [];
    } catch {
      return; // 轮询失败保持现状，下次再试
    }
    const live = new Set(items.map((i) => i.request_id));
    for (const id of [...dismissedRef.current]) {
      if (!live.has(id)) dismissedRef.current.delete(id);
    }
    setCurrent((prev) => {
      if (prev && live.has(prev.id) && !dismissedRef.current.has(prev.id)) return prev;
      const next = items.find((i) => !dismissedRef.current.has(i.request_id));
      return next ? toRequest(next) : null;
    });
  }, []);

  useEffect(() => {
    void poll();
    const t = setInterval(() => {
      void poll();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [poll]);

  const handleDecide = useCallback(
    async (requestId: number, decision: "allow" | "deny", scope: "once" | "session") => {
      try {
        await api.decideMcpApproval(requestId, decision, scope);
      } finally {
        // 无论成功还是已被别处决策（404），都刷新权威 pending 列表
        setCurrent(null);
        await poll();
      }
    },
    [poll]
  );

  const handleClose = useCallback(() => {
    setCurrent((prev) => {
      if (prev) dismissedRef.current.add(prev.id);
      return null;
    });
  }, []);

  if (!current) return null;
  return (
    <McpApprovalDialog
      open
      request={current}
      onDecide={handleDecide}
      onClose={handleClose}
    />
  );
}
