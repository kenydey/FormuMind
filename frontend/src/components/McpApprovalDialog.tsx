import { useState } from "react";
import Modal from "./Modal";

export type McpApprovalDecision = "allow" | "deny";
/** 决策作用域：allow 持久化到后端策略表（见 mcp_approval.decide_approval），deny 仅标记请求。 */
export type McpApprovalScope = "once" | "session";

/** 与后端 mcp_approval_requests 表字段对齐；arguments 后端暂未持久化，可选。 */
export interface McpApprovalRequest {
  id: number;
  server_id: string;
  tool_name: string;
  session_id?: string | null;
  project_id?: string | null;
  arguments?: Record<string, unknown> | null;
  requested_at?: number | null;
}

interface Props {
  open: boolean;
  request: McpApprovalRequest | null;
  /** 提交决策：由 McpApprovalCenter 接后端 REST 接口
     （POST /api/mcp/approvals/{id}/decide）。 */
  onDecide: (
    requestId: number,
    decision: McpApprovalDecision,
    scope: McpApprovalScope
  ) => Promise<void>;
  onClose: () => void;
}

/**
 * W3-11: MCP 工具审批对话框。
 * 后端 Wave 2 默认 ask→deny+审计；此对话框展示 pending 审批并提交用户决策。
 */
export default function McpApprovalDialog({ open, request, onDecide, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open || !request) return null;

  async function decide(decision: McpApprovalDecision, scope: McpApprovalScope) {
    setBusy(true);
    setError(null);
    try {
      await onDecide(request!.id, decision, scope);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const argsJson =
    request.arguments != null ? JSON.stringify(request.arguments, null, 2) : null;

  return (
    <Modal
      title="MCP 工具审批"
      open={open}
      onClose={onClose}
      nested
      testId="modal-mcp-approval"
    >
      <div className="space-y-3" data-testid="mcp-approval-dialog">
        <p className="text-xs text-slate-500 leading-relaxed">
          以下 MCP 工具调用被策略拦截（默认 ask），等待你的审批。无决策时后端 5
          分钟后自动拒绝。
        </p>

        <dl className="text-sm space-y-1.5 rounded border border-edge/60 px-3 py-2">
          <div className="flex gap-2">
            <dt className="text-slate-500 text-xs w-16 shrink-0 pt-0.5">服务器</dt>
            <dd className="text-slate-200 font-mono text-xs break-all" data-testid="mcp-approval-server">
              {request.server_id}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-slate-500 text-xs w-16 shrink-0 pt-0.5">工具</dt>
            <dd className="text-slate-200 font-mono text-xs break-all" data-testid="mcp-approval-tool">
              {request.tool_name}
            </dd>
          </div>
          {request.session_id && (
            <div className="flex gap-2">
              <dt className="text-slate-500 text-xs w-16 shrink-0 pt-0.5">会话</dt>
              <dd className="text-slate-400 font-mono text-xs break-all">{request.session_id}</dd>
            </div>
          )}
          <div className="flex gap-2">
            <dt className="text-slate-500 text-xs w-16 shrink-0 pt-0.5">请求 ID</dt>
            <dd className="text-slate-400 font-mono text-xs">{request.id}</dd>
          </div>
        </dl>

        <div>
          <p className="text-xs text-slate-500 mb-1">调用参数</p>
          {argsJson ? (
            <pre
              data-testid="mcp-approval-args"
              className="text-[11px] font-mono bg-ink border border-edge rounded px-3 py-2 overflow-auto max-h-48 text-slate-300"
            >
              {argsJson}
            </pre>
          ) : (
            <p className="text-[11px] text-slate-600 border border-edge/40 rounded px-3 py-2">
              （参数未记录）
            </p>
          )}
        </div>

        {error && (
          <div className="text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
            提交失败：{error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={() => void decide("deny", "once")}
            disabled={busy}
            data-testid="mcp-approval-deny"
            className="text-sm border border-rose-500/40 text-rose-300 rounded px-4 py-1.5 hover:bg-rose-500/10 disabled:opacity-40"
          >
            {busy ? "提交中…" : "拒绝"}
          </button>
          <button
            onClick={() => void decide("allow", "once")}
            disabled={busy}
            data-testid="mcp-approval-allow-once"
            className="text-sm border border-edge text-slate-300 rounded px-4 py-1.5 hover:border-accent/40 hover:text-accent disabled:opacity-40"
          >
            {busy ? "提交中…" : "允许一次"}
          </button>
          <button
            onClick={() => void decide("allow", "session")}
            disabled={busy}
            data-testid="mcp-approval-allow-session"
            className="text-sm bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-4 py-1.5 disabled:opacity-40"
          >
            允许本次会话
          </button>
        </div>
      </div>
    </Modal>
  );
}
