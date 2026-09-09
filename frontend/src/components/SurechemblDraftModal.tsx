/**
 * SureChEMBL P3 — human review gate for embodiment Formulation drafts.
 * Confirm writes KG + pending materials only (never production formula pool).
 */
import { useState } from "react";
import Modal from "./Modal";
import { api, type SurechemblExampleDraft } from "../api";

export default function SurechemblDraftModal({
  draft,
  onClose,
  onConfirmed,
}: {
  draft: SurechemblExampleDraft;
  onClose: () => void;
  onConfirmed?: (note: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneNote, setDoneNote] = useState<string | null>(null);
  const form = draft.formulation;
  const ingredients = form?.ingredients ?? [];

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.surechemblConfirmExampleDraft(draft);
      if (!res.ok) {
        setError("确认失败");
        return;
      }
      if (res.promoted_to_pool) {
        setError("安全闸失败：不应写入生产配方池");
        return;
      }
      const pending = (res.pending_materials || []).filter((p) => p.action === "pending").length;
      const note =
        res.note ||
        `已确认：${pending} 条原料进入 pending；配方实体 ${res.formulation_entity_id}；未写入生产配方池`;
      setDoneNote(note);
      onConfirmed?.(note);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`🧪 实施例草稿 · ${draft.doc_id}`}
      open
      onClose={onClose}
      size="lg"
      testId="modal-surechembl-draft"
      onSave={doneNote ? undefined : () => void confirm()}
      saveLabel={busy ? "确认中…" : "人工确认入库"}
    >
      <div className="space-y-3 text-sm" data-testid="surechembl-draft-review">
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
          人审闸：重量分为占位均分，非专利真实配比。确认后仅写入 KG + 原料 pending，
          <strong className="text-amber-100"> 不会</strong> 写入生产配方池或静默 upsert 原料。
        </div>
        <div className="text-[11px] text-slate-400 space-y-0.5">
          <div>
            <span className="text-slate-500">标题</span> {draft.title || draft.doc_id}
          </div>
          {draft.assignee && (
            <div>
              <span className="text-slate-500">专利权人</span> {draft.assignee}
            </div>
          )}
          {draft.pub_date && (
            <div>
              <span className="text-slate-500">公开日</span> {draft.pub_date}
            </div>
          )}
          <div>
            <span className="text-slate-500">origin</span>{" "}
            <code className="text-accent2">{draft.origin}</code> · needs_review=
            {String(draft.needs_review)}
          </div>
          {(draft.url || draft.url_alt) && (
            <div className="flex gap-2">
              {draft.url && (
                <a href={draft.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                  Patents
                </a>
              )}
              {draft.url_alt && (
                <a
                  href={draft.url_alt}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent/80 hover:underline"
                >
                  SureChEMBL
                </a>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
            Formulation 草稿 · {form?.name}
          </div>
          <div className="border border-edge rounded overflow-hidden">
            <table className="w-full text-[11px]">
              <thead className="bg-ink/60 text-slate-500">
                <tr>
                  <th className="text-left px-2 py-1 font-medium">原料</th>
                  <th className="text-left px-2 py-1 font-medium">角色</th>
                  <th className="text-right px-2 py-1 font-medium">wt%*</th>
                </tr>
              </thead>
              <tbody>
                {ingredients.map((ing, i) => (
                  <tr key={`${ing.name}-${i}`} className="border-t border-edge/50">
                    <td className="px-2 py-1 text-slate-200">{ing.name}</td>
                    <td className="px-2 py-1 text-slate-500">{ing.role}</td>
                    <td className="px-2 py-1 text-right font-mono text-slate-400">
                      {ing.weight_pct}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-slate-600 mt-1">* 占位均分，须人审后才能使用</p>
        </div>

        {form?.warnings?.length ? (
          <ul className="text-[11px] text-slate-400 list-disc pl-4 space-y-0.5">
            {form.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}

        {error && <div className="text-xs text-rose-400 bg-rose-500/10 rounded p-2">{error}</div>}
        {doneNote && (
          <div
            className="text-xs text-emerald-300 bg-emerald-500/10 rounded p-2"
            data-testid="surechembl-draft-confirmed"
          >
            {doneNote}
          </div>
        )}
      </div>
    </Modal>
  );
}
