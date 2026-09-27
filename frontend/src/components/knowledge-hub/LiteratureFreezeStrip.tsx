import { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "../../api";

type Props = {
  projectId: string | null;
};

type Manifest = Awaited<ReturnType<typeof api.getLiteratureManifest>>;

/** Compact Wave B strip: capture / freeze / light screening for project literature. */
export default function LiteratureFreezeStrip({ projectId }: Props) {
  const [man, setMan] = useState<Manifest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeKw, setIncludeKw] = useState("epoxy,coating");
  const [excludeKw, setExcludeKw] = useState("");
  const [showScreen, setShowScreen] = useState(false);
  const [enrichMsg, setEnrichMsg] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!projectId) {
      setMan(null);
      return;
    }
    try {
      const m = await api.getLiteratureManifest(projectId);
      setMan(m);
      setError(null);
    } catch (e) {
      setError(formatApiError(e));
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!projectId) {
    return (
      <div
        className="rounded border border-edge px-2 py-1.5 text-[11px] text-slate-500"
        data-testid="literature-freeze-strip"
      >
        文献冻结：请先选择活动项目
      </div>
    );
  }

  const frozen = man?.frozen;
  const digestShort = frozen?.digest ? frozen.digest.slice(0, 10) : "—";
  const cand = man?.coverage?.candidate_count ?? 0;
  const froz = man?.coverage?.frozen_count ?? 0;

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      className="rounded border border-violet-500/30 bg-violet-500/5 px-2 py-2 space-y-1.5 text-[11px]"
      data-testid="literature-freeze-strip"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-violet-200 font-medium">文献冻结</span>
        <span className="text-slate-400" data-testid="literature-freeze-stats">
          候选 {cand} · 已冻 {froz} · digest {digestShort}
        </span>
        <button
          type="button"
          disabled={!!busy}
          className="px-1.5 py-0.5 rounded border border-edge text-slate-300 hover:border-accent/40 disabled:opacity-40"
          data-testid="literature-capture-btn"
          onClick={() =>
            run("capture", () => api.captureLiteratureManifest({ project_id: projectId }))
          }
        >
          Capture
        </button>
        <button
          type="button"
          disabled={!!busy}
          className="px-1.5 py-0.5 rounded border border-violet-500/40 text-violet-200 hover:bg-violet-500/10 disabled:opacity-40"
          data-testid="literature-freeze-btn"
          onClick={() =>
            run("freeze", () =>
              api.freezeLiteratureManifest({ project_id: projectId, actor: "hub" }),
            )
          }
        >
          Freeze
        </button>
        <button
          type="button"
          disabled={!!busy || !frozen}
          className="px-1.5 py-0.5 rounded border border-edge text-slate-400 disabled:opacity-40"
          data-testid="literature-unfreeze-btn"
          onClick={() =>
            run("unfreeze", () =>
              api.unfreezeLiteratureManifest({ project_id: projectId, actor: "hub" }),
            )
          }
        >
          Unfreeze
        </button>
        <button
          type="button"
          disabled={!!busy || cand < 1}
          className="px-1.5 py-0.5 rounded border border-sky-500/40 text-sky-200 hover:bg-sky-500/10 disabled:opacity-40"
          data-testid="literature-enrich-oa-btn"
          onClick={() =>
            run("enrich-oa", async () => {
              const res = await api.enrichLiteratureOa({
                project_id: projectId,
                scope: "missing_fulltext",
                limit: 20,
                actor: "hub",
              });
              setEnrichMsg(
                `补全文 ${res.fetched ?? 0}/${res.attempted ?? 0}` +
                  (res.skipped ? ` · 跳过 ${res.skipped}` : ""),
              );
            })
          }
        >
          补全文
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded border border-edge text-slate-400"
          data-testid="literature-screen-toggle"
          onClick={() => setShowScreen((v) => !v)}
        >
          筛选
        </button>
      </div>
      {enrichMsg && (
        <div className="text-[10px] text-sky-300/90" data-testid="literature-enrich-msg">
          {enrichMsg}
        </div>
      )}

      {showScreen && (
        <div className="space-y-1 border-t border-edge/60 pt-1.5" data-testid="literature-screen-form">
          <label className="flex gap-1 items-center text-slate-400">
            纳入
            <input
              className="flex-1 bg-ink border border-edge rounded px-1 py-0.5 text-slate-200"
              value={includeKw}
              onChange={(e) => setIncludeKw(e.target.value)}
              data-testid="literature-include-kw"
            />
          </label>
          <label className="flex gap-1 items-center text-slate-400">
            排除
            <input
              className="flex-1 bg-ink border border-edge rounded px-1 py-0.5 text-slate-200"
              value={excludeKw}
              onChange={(e) => setExcludeKw(e.target.value)}
              data-testid="literature-exclude-kw"
            />
          </label>
          <button
            type="button"
            disabled={!!busy}
            className="px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-200 disabled:opacity-40"
            data-testid="literature-screen-btn"
            onClick={() =>
              run("screen", () =>
                api.screenLiteratureManifest({
                  project_id: projectId,
                  apply: true,
                  criteria: {
                    include_keywords: includeKw
                      .split(/[,，]/)
                      .map((s) => s.trim())
                      .filter(Boolean),
                    exclude_keywords: excludeKw
                      .split(/[,，]/)
                      .map((s) => s.trim())
                      .filter(Boolean),
                  },
                }),
              )
            }
          >
            运行筛选
          </button>
          {man?.items && man.items.length > 0 && (
            <ul className="max-h-24 overflow-auto text-slate-400 space-y-0.5">
              {man.items.slice(0, 12).map((it) => (
                <li key={it.id}>
                  <span
                    className={
                      it.screening === "match"
                        ? "text-emerald-400"
                        : it.screening === "no_match"
                          ? "text-rose-400"
                          : "text-amber-300"
                    }
                  >
                    [{it.screening || "unset"}]
                  </span>{" "}
                  {(it.title || it.id).slice(0, 48)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {error && (
        <p className="text-rose-400" data-testid="literature-freeze-error">
          {error}
        </p>
      )}
      {busy && <p className="text-slate-500">…{busy}</p>}
    </div>
  );
}
