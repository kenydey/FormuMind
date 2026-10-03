/**
 * W5-4 (P1-28): Reviewer 审计页（Modal 形态）—— review runs 列表 + 单 run
 * 详情：action log（claim 级 disposition 时间线）、stale 提示、手动重审。
 *
 * 从 ReviewerCard 的"审计详情"打开（带 initialRunId + 问答上下文可重审）；
 * 无 initialRunId 时为纯列表浏览，此时重审按钮禁用（缺问答输入）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import {
  reviewsApi,
  formatApiError,
  type ReviewChecklist,
  type ReviewRunDetail,
  type ReviewRunSummary,
} from "../api";

const DISPOSITION_CLS: Record<string, string> = {
  resolved: "text-emerald-300",
  open: "text-amber-300",
  unaddressed: "text-rose-300",
};

const VERDICT_LABEL: Record<string, string> = { pass: "通过", flagged: "标记", n_a: "不适用" };
const VERDICT_CLS: Record<string, string> = {
  pass: "text-emerald-300",
  flagged: "text-amber-300",
  n_a: "text-slate-400",
};
const CATEGORY_LABEL: Record<string, string> = {
  citation: "引用",
  numeric: "数值",
  method: "方法",
  general: "通用",
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
  const [checklist, setChecklist] = useState<ReviewChecklist | null>(null);
  const [checklistBusy, setChecklistBusy] = useState(false);
  const [checklistError, setChecklistError] = useState<string | null>(null);
  /** F-4: 列表/详情各自的序号守卫 —— 快速切换时旧请求不覆盖新数据。 */
  const listSeq = useRef(0);
  const detailSeq = useRef(0);
  const checklistSeq = useRef(0);

  const loadList = useCallback(async () => {
    const seq = ++listSeq.current;
    setBusy(true);
    setError(null);
    try {
      const res = await reviewsApi.listReviewRuns({
        projectId: projectId ?? undefined,
        limit: 30,
      });
      if (listSeq.current !== seq) return;
      setRuns(res.items ?? []);
    } catch (e) {
      if (listSeq.current !== seq) return;
      setError(formatApiError(e));
    } finally {
      if (listSeq.current === seq) setBusy(false);
    }
  }, [projectId]);

  const loadDetail = useCallback(async (id: string) => {
    const seq = ++detailSeq.current;
    setBusy(true);
    setError(null);
    try {
      const d = await reviewsApi.getReviewRun(id);
      if (detailSeq.current !== seq) return;
      setDetail(d);
      setSelectedId(id);
    } catch (e) {
      if (detailSeq.current !== seq) return;
      setError(formatApiError(e));
    } finally {
      if (detailSeq.current === seq) setBusy(false);
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

  // 清单属于某一条审计记录：换记录就丢掉上一条的（含其在途请求）。
  const detailRunId = detail?.run_id ?? null;
  useEffect(() => {
    checklistSeq.current += 1;
    setChecklist(null);
    setChecklistError(null);
    setChecklistBusy(false);
  }, [detailRunId]);

  async function loadChecklist() {
    if (!detailRunId) return;
    const seq = ++checklistSeq.current;
    setChecklistBusy(true);
    setChecklistError(null);
    try {
      const c = await reviewsApi.getReviewChecklist(detailRunId);
      if (checklistSeq.current !== seq) return;
      setChecklist(c);
    } catch (e) {
      if (checklistSeq.current !== seq) return;
      setChecklistError(formatApiError(e));
    } finally {
      if (checklistSeq.current === seq) setChecklistBusy(false);
    }
  }

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
              <div className="mt-3 border-t border-edge/60 pt-2" data-testid="reviewer-checklist">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[11px] text-slate-500">发布清单（逐条 通过 / 标记）</span>
                  <button
                    type="button"
                    data-testid="reviewer-checklist-load"
                    onClick={() => void loadChecklist()}
                    disabled={checklistBusy}
                    className="text-[11px] px-2 py-0.5 rounded border border-edge text-slate-300 hover:border-accent/50 hover:text-accent disabled:opacity-40"
                    title="按本次审计的处置记录生成结构化清单"
                  >
                    {checklistBusy ? "生成中…" : checklist ? "刷新" : "生成清单"}
                  </button>
                </div>
                {checklistError && (
                  <div className="text-[11px] text-rose-300/80">{checklistError}</div>
                )}
                {checklist && (
                  <div>
                    <div className="flex gap-3 text-[11px] mb-1" data-testid="reviewer-checklist-summary">
                      <span className={VERDICT_CLS.pass}>通过 {checklist.summary.pass}</span>
                      <span className={VERDICT_CLS.flagged}>标记 {checklist.summary.flagged}</span>
                      <span className={VERDICT_CLS.n_a}>不适用 {checklist.summary.n_a}</span>
                    </div>
                    {checklist.items.length === 0 ? (
                      <div className="text-[11px] text-slate-500">本次审计没有可核对的条目。</div>
                    ) : (
                      <ul className="space-y-1">
                        {checklist.items.map((it) => (
                          <li
                            key={it.id}
                            data-testid="reviewer-checklist-item"
                            className="flex items-start gap-2 text-[11px]"
                          >
                            <span className={`shrink-0 ${VERDICT_CLS[it.verdict] ?? "text-slate-400"}`}>
                              {VERDICT_LABEL[it.verdict] ?? it.verdict}
                            </span>
                            <span className="shrink-0 text-slate-500">
                              {CATEGORY_LABEL[it.category] ?? it.category}
                            </span>
                            <span className="text-slate-300 break-words min-w-0">{it.statement}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
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
