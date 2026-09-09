/**
 * P3.1 — human review gate for embodiment Formulation drafts (fulltext or SureChEMBL).
 * Confirm writes KG + pending materials only (never production formula pool).
 */
import { useMemo, useState } from "react";
import Modal from "./Modal";
import { api, type EmbodimentDraft } from "../api";

function isFulltextOrigin(origin: string): boolean {
  return origin !== "surechembl";
}

export default function EmbodimentDraftModal({
  draft: initial,
  onClose,
  onConfirmed,
}: {
  draft: EmbodimentDraft;
  onClose: () => void;
  onConfirmed?: (note: string) => void;
}) {
  const [embIdx, setEmbIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneNote, setDoneNote] = useState<string | null>(null);

  const embodiments = initial.embodiments ?? [];
  const activeEmb = embodiments[embIdx];

  const draft = useMemo(() => {
    if (!activeEmb) return initial;
    const ingredients = activeEmb.ingredients ?? [];
    return {
      ...initial,
      amount_source: activeEmb.amount_source || initial.amount_source,
      ingredients_detail: ingredients,
      formulation: {
        ...initial.formulation,
        ingredients: ingredients.map((ing) => ({
          name: ing.name,
          role: ing.role || "additive",
          weight_pct: ing.weight_pct,
        })),
      },
    };
  }, [initial, activeEmb]);

  const form = draft.formulation;
  const ingredients = form?.ingredients ?? [];
  const amountSource = draft.amount_source || "placeholder";
  const isPlaceholder = amountSource === "placeholder";
  const hasIngredients = ingredients.length > 0;
  const canConfirm = hasIngredients && !doneNote;

  async function confirm() {
    if (!hasIngredients) {
      setError("无可确认组分：未识别到配方表");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = isFulltextOrigin(draft.origin)
        ? await api.confirmEmbodimentDraft(draft)
        : await api.surechemblConfirmExampleDraft(draft);
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
      title={`🧪 实施例草稿 · ${draft.doc_id || draft.source_id || ""}`}
      open
      onClose={onClose}
      size="lg"
      testId="modal-embodiment-draft"
      onSave={canConfirm ? () => void confirm() : undefined}
      saveLabel={busy ? "确认中…" : "人工确认入库"}
      saveDisabled={busy || !canConfirm}
    >
      <div className="space-y-3 text-sm" data-testid="embodiment-draft-review">
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
          人审闸：确认后仅写入 KG + 原料 pending，
          <strong className="text-amber-100"> 不会</strong> 写入生产配方池或静默 upsert 原料。
          {isPlaceholder
            ? hasIngredients
              ? " 当前比重为占位均分，非原文真实配比。"
              : " 未识别到可用配方表或组分——请改用含表格的 PDF 再入库全文，或人工填写后再确认。"
            : " 比重来自已入库全文表格（已归一），请核对原件。"}
        </div>

        {embodiments.length > 1 && (
          <div className="flex flex-wrap gap-1" data-testid="embodiment-tabs">
            {embodiments.map((e, i) => (
              <button
                key={`${e.label}-${i}`}
                type="button"
                onClick={() => setEmbIdx(i)}
                className={`text-[10px] border rounded-full px-2 py-0.5 ${
                  i === embIdx
                    ? "border-accent2 text-accent2 bg-accent2/10"
                    : "border-edge text-slate-500 hover:text-accent"
                }`}
              >
                {e.label || `Example ${i + 1}`}
              </button>
            ))}
          </div>
        )}

        <div className="text-[11px] text-slate-400 space-y-0.5">
          <div>
            <span className="text-slate-500">标题</span> {draft.title || draft.doc_id}
          </div>
          {draft.source_id && (
            <div>
              <span className="text-slate-500">source_id</span>{" "}
              <code className="text-[10px]">{draft.source_id}</code>
            </div>
          )}
          {draft.assignee && (
            <div>
              <span className="text-slate-500">专利权人</span> {draft.assignee}
            </div>
          )}
          <div>
            <span className="text-slate-500">origin</span>{" "}
            <code className="text-accent2">{draft.origin}</code>
            {" · "}
            <span className="text-slate-500">amount_source</span>{" "}
            <code data-testid="embodiment-amount-source">{amountSource}</code>
            {" · needs_review="}
            {String(draft.needs_review)}
          </div>
          {(draft.url || draft.url_alt) && (
            <div className="flex gap-2">
              {draft.url && (
                <a href={draft.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                  Source
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
                  <th className="text-right px-2 py-1 font-medium">wt%</th>
                </tr>
              </thead>
              <tbody>
                {ingredients.length === 0 ? (
                  <tr className="border-t border-edge/50">
                    <td
                      colSpan={3}
                      className="px-2 py-3 text-slate-500 text-center"
                      data-testid="embodiment-empty-ingredients"
                    >
                      无可抽取组分（未识别到配方表）
                    </td>
                  </tr>
                ) : (
                  ingredients.map((ing, i) => (
                    <tr key={`${ing.name}-${i}`} className="border-t border-edge/50">
                      <td className="px-2 py-1 text-slate-200">{ing.name}</td>
                      <td className="px-2 py-1 text-slate-500">{ing.role}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-400">
                        {ing.weight_pct}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-slate-600 mt-1">
            {!hasIngredients
              ? "* 空草稿不可确认入库；请先获得含表全文"
              : isPlaceholder
                ? "* 占位均分，须人审后才能使用"
                : "* 来自全文表，请人审后使用"}
          </p>
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
            data-testid="embodiment-draft-confirmed"
          >
            {doneNote}
          </div>
        )}
      </div>
    </Modal>
  );
}
