import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, formatApiError, type PreflightFinding, type PreflightState } from "../api";

/** Report kinds that go through publication preflight on export. */
export const PREFLIGHT_KINDS: Array<{ key: string; label: string }> = [
  { key: "storm", label: "STORM 长文" },
  { key: "tech_report_formulation", label: "配方技术报告" },
  { key: "tech_report_doe", label: "DOE 技术报告" },
  { key: "tech_report_optimization", label: "优化技术报告" },
];

const SEVERITY_ORDER: Record<string, number> = { blocking: 0, major: 1, minor: 2, info: 3 };

export function severityTone(severity: string): string {
  if (severity === "blocking") return "text-rose-300 border-rose-500/40";
  if (severity === "major") return "text-amber-300 border-amber-500/40";
  return "text-slate-400 border-edge";
}

/** Blocking+open first, then by severity, then still-open before handled. */
export function sortFindings(findings: PreflightFinding[]): PreflightFinding[] {
  const rank = (f: PreflightFinding) =>
    (f.status === "open" ? 0 : 10) + (SEVERITY_ORDER[f.severity] ?? 5);
  return [...findings].sort((a, b) => rank(a) - rank(b));
}

type Edit = { id: string; mode: "override" | "resolve"; actor: string; text: string };

type Props = {
  projectId: string | null;
  /** ``publication_preflight_enabled``; the host already reads the flags. */
  enabled?: boolean;
};

/**
 * 发布预检面板 —— 导出报告前的引用 / 占位符 / 数值闸门。
 *
 * 阻断项未处理时导出会被 409 拒绝；此前界面只能看到一句"预检未通过"，没有任何
 * 入口查看原因或放行。本面板读取 ``/api/wiki/preflight``：列出发现项，支持
 * 「标记已处理」（resolve，需备注）与「放行」（override，需 actor + 理由，留痕），
 * 并可重新预检 / 定稿。条目内容本身不在此编辑。
 */
export default function PublicationPreflightPanel({ projectId, enabled = true }: Props) {
  const [kind, setKind] = useState(PREFLIGHT_KINDS[0].key);
  const [state, setState] = useState<PreflightState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    if (!projectId) {
      setState(null);
      return;
    }
    try {
      const s = await api.getPreflightState(projectId, kind);
      if (loadSeq.current !== seq) return;
      setState(s);
      setError(null);
    } catch (e) {
      if (loadSeq.current !== seq) return;
      setState(null);
      setError(formatApiError(e));
    }
  }, [projectId, kind]);

  useEffect(() => {
    setEdit(null);
    if (enabled) void load();
  }, [load, enabled]);

  const findings = useMemo(() => sortFindings(state?.findings ?? []), [state]);

  if (!enabled) return null;

  if (!projectId) {
    return (
      <div
        className="rounded border border-edge px-2 py-1.5 text-[11px] text-slate-500"
        data-testid="preflight-panel"
      >
        发布预检：请先选择活动项目
      </div>
    );
  }

  const run = async (label: string, fn: () => Promise<PreflightState | null>) => {
    if (busy) return;
    setBusy(label);
    setError(null);
    try {
      const next = await fn();
      if (next) setState(next);
    } catch (e) {
      // finalize answers 409 with {errors, state}: show the reasons, not raw JSON.
      const detail = e instanceof ApiError ? (e.detail as { errors?: string[] } | undefined) : undefined;
      if (detail && Array.isArray(detail.errors) && detail.errors.length > 0) {
        setError(detail.errors.join("；"));
      } else {
        setError(formatApiError(e));
      }
    } finally {
      setBusy(null);
    }
  };

  const review = () =>
    run("review", () => api.reviewPreflight({ project_id: projectId, kind }));

  const finalize = () =>
    run("finalize", async () => {
      const res = await api.finalizePreflight({ project_id: projectId, kind, actor: "user" });
      return res.state;
    });

  const submitEdit = () => {
    if (!edit) return;
    const actor = edit.actor.trim();
    const text = edit.text.trim();
    if (!actor || !text) {
      setError(edit.mode === "override" ? "放行需要填写操作人与理由" : "请填写操作人与处理备注");
      return;
    }
    void run(edit.mode, async () => {
      const next =
        edit.mode === "override"
          ? await api.overridePreflightFinding({
              project_id: projectId,
              kind,
              finding_id: edit.id,
              actor,
              reason: text,
            })
          : await api.resolvePreflightFinding({
              project_id: projectId,
              kind,
              finding_id: edit.id,
              actor,
              note: text,
            });
      setEdit(null);
      return next;
    });
  };

  const openBlocking = state?.open_blocking ?? 0;
  const finalizedAt = state?.finalization?.at
    ? new Date(state.finalization.at * 1000).toLocaleString("zh-CN", { hour12: false })
    : "";

  return (
    <div
      className="rounded border border-edge/70 bg-ink/40 px-3 py-2 space-y-2 text-[11px]"
      data-testid="preflight-panel"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-slate-200 font-medium">发布预检</span>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="bg-ink border border-edge rounded px-1 py-0.5 text-slate-300"
          data-testid="preflight-kind"
        >
          {PREFLIGHT_KINDS.map((k) => (
            <option key={k.key} value={k.key}>
              {k.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="ml-auto px-1.5 py-0.5 rounded border border-edge text-slate-400 hover:border-accent/40"
          onClick={() => void load()}
          data-testid="preflight-reload-btn"
        >
          刷新
        </button>
        {kind === "storm" && (
          <button
            type="button"
            disabled={busy !== null}
            className="px-1.5 py-0.5 rounded border border-edge text-slate-300 hover:border-accent/40 disabled:opacity-50"
            title="按当前已生成的 STORM 长文重新跑引用 / 占位符 / 数值检查"
            onClick={() => void review()}
            data-testid="preflight-review-btn"
          >
            {busy === "review" ? "预检中…" : "重新预检"}
          </button>
        )}
        <button
          type="button"
          disabled={busy !== null || openBlocking > 0 || !state?.content_hash}
          className="px-1.5 py-0.5 rounded border border-accent/50 text-accent disabled:opacity-40"
          title="无未处理阻断项时定稿；内容再变更后需重新预检"
          onClick={() => void finalize()}
          data-testid="preflight-finalize-btn"
        >
          {busy === "finalize" ? "定稿中…" : "定稿"}
        </button>
      </div>

      <p className="text-[10px] text-slate-400" data-testid="preflight-summary">
        {!state || !state.content_hash
          ? "尚未预检：先生成报告并导出一次（或点「重新预检」）。"
          : openBlocking > 0
            ? `${openBlocking} 条阻断项未处理 —— 导出会被拒绝；处理或放行后可重试。`
            : state.ready
              ? `预检通过，已定稿${finalizedAt ? ` · ${finalizedAt}` : ""}${state.finalization?.actor ? ` · ${state.finalization.actor}` : ""}。`
              : "无未处理阻断项，导出时将自动定稿。"}
        {state && state.open_major > 0 ? ` 另有 ${state.open_major} 条主要问题待查看。` : ""}
      </p>

      {error && (
        <p className="text-rose-400" data-testid="preflight-error">
          {error}
        </p>
      )}

      {findings.length > 0 && (
        <ul className="max-h-64 overflow-auto space-y-1" data-testid="preflight-findings">
          {findings.map((f) => {
            const handled = f.status !== "open";
            return (
              <li
                key={f.id}
                className="rounded border border-edge/50 px-2 py-1 space-y-1"
                data-testid={`preflight-finding-${f.id}`}
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={`px-1 rounded border text-[10px] ${severityTone(f.severity)}`}>
                    {f.severity}
                  </span>
                  <span className="text-slate-500 text-[10px]">{f.check}</span>
                  <span className="text-slate-200">{f.title}</span>
                  {f.stale && (
                    <span className="text-amber-300 text-[10px]" title="内容已变更，需重新确认">
                      stale
                    </span>
                  )}
                  <span
                    className={`ml-auto text-[10px] ${handled ? "text-emerald-400" : "text-amber-300"}`}
                    data-testid={`preflight-status-${f.id}`}
                  >
                    {f.status === "open" ? "待处理" : f.status === "overridden" ? "已放行" : "已处理"}
                  </span>
                </div>
                {f.detail && <div className="text-slate-400">{f.detail}</div>}
                {(f.evidence ?? []).length > 0 && (
                  <div className="text-slate-500 text-[10px] truncate" title={(f.evidence ?? []).join(" | ")}>
                    {(f.evidence ?? []).slice(0, 2).join(" · ")}
                  </div>
                )}
                {handled && f.resolution && (
                  <div className="text-slate-500 text-[10px]">
                    {f.resolution.actor ?? "—"}：{f.resolution.reason ?? f.resolution.note ?? ""}
                  </div>
                )}
                {!handled && edit?.id !== f.id && (
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      className="px-1 rounded border border-edge text-slate-300 hover:border-accent/40"
                      onClick={() => setEdit({ id: f.id, mode: "resolve", actor: "user", text: "" })}
                      data-testid={`preflight-resolve-${f.id}`}
                    >
                      标记已处理
                    </button>
                    <button
                      type="button"
                      className="px-1 rounded border border-amber-500/40 text-amber-300 hover:border-amber-400"
                      title="接受该风险并放行（留痕：操作人 + 理由）"
                      onClick={() => setEdit({ id: f.id, mode: "override", actor: "user", text: "" })}
                      data-testid={`preflight-override-${f.id}`}
                    >
                      放行…
                    </button>
                  </div>
                )}
                {edit?.id === f.id && (
                  <form
                    className="flex flex-wrap items-center gap-1"
                    noValidate
                    onSubmit={(e) => {
                      e.preventDefault();
                      submitEdit();
                    }}
                    data-testid={`preflight-edit-${f.id}`}
                  >
                    <input
                      value={edit.actor}
                      maxLength={120}
                      onChange={(e) => setEdit({ ...edit, actor: e.target.value })}
                      placeholder="操作人"
                      className="w-20 bg-ink border border-edge rounded px-1 py-0.5 text-slate-200"
                      data-testid="preflight-action-actor"
                    />
                    <input
                      value={edit.text}
                      maxLength={2000}
                      onChange={(e) => setEdit({ ...edit, text: e.target.value })}
                      placeholder={edit.mode === "override" ? "放行理由（必填）" : "处理备注（必填）"}
                      className="flex-1 min-w-40 bg-ink border border-edge rounded px-1 py-0.5 text-slate-200"
                      data-testid="preflight-action-text"
                    />
                    <button
                      type="submit"
                      disabled={busy !== null}
                      className="px-1.5 py-0.5 rounded border border-accent/50 text-accent disabled:opacity-50"
                      data-testid="preflight-action-submit"
                    >
                      {edit.mode === "override" ? "确认放行" : "确认已处理"}
                    </button>
                    <button
                      type="button"
                      className="px-1.5 py-0.5 rounded border border-edge text-slate-400"
                      onClick={() => setEdit(null)}
                      data-testid="preflight-action-cancel"
                    >
                      取消
                    </button>
                  </form>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
