import { useCallback, useEffect, useRef, useState } from "react";
import { api, awaitTaskStream, formatApiError } from "../../api";

type Props = {
  projectId: string | null;
};

type Manifest = Awaited<ReturnType<typeof api.getLiteratureManifest>>;
type RuleVersion = Awaited<
  ReturnType<typeof api.getScreeningRuleVersions>
>["versions"][number];
type RuleHistory = Awaited<
  ReturnType<typeof api.getScreeningRuleVersions>
>["history"][number];
type EvalResult = Awaited<ReturnType<typeof api.evaluateScreening>>;

/** Compact Wave B strip: capture / freeze / light screening for project literature. */
export default function LiteratureFreezeStrip({ projectId }: Props) {
  const [man, setMan] = useState<Manifest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeKw, setIncludeKw] = useState("epoxy,coating");
  const [excludeKw, setExcludeKw] = useState("");
  const [showScreen, setShowScreen] = useState(false);
  const [enrichMsg, setEnrichMsg] = useState<string | null>(null);
  // W6-2: 规则套件
  const [presets, setPresets] = useState<Array<{ name: string; title: string }>>(
    [],
  );
  const [preset, setPreset] = useState("");
  const [ruleName, setRuleName] = useState("");
  const [versions, setVersions] = useState<RuleVersion[]>([]);
  const [history, setHistory] = useState<RuleHistory[]>([]);
  const [currentRuleName, setCurrentRuleName] = useState<string | null>(null);
  const [evalRes, setEvalRes] = useState<EvalResult | null>(null);
  /** F-5: 回滚 select 受控化（替代 document.getElementById 命令式读取）。 */
  const [rollbackVersion, setRollbackVersion] = useState("");
  /** F-4: 序号守卫 —— project 快速切换时旧请求不覆盖新数据。 */
  const loadSeq = useRef(0);
  /** F-4: run() 动作耗时中 project 可能切换；reloadRef 始终指向最新 projectId 的
   *  reload，避免动作完成后的刷新用旧闭包把旧 project 的 manifest 写回新面板
   *  （旧闭包的 seq 反而是最新，会绕过序号守卫）。 */
  const reloadRef = useRef<() => Promise<void>>(async () => {});
  /** F-4: 零散 setState（评估结果等）用 projectId 快照比对，丢弃过期 project 的写入。 */
  const projectIdRef = useRef<string | null>(projectId);

  const kwCriteria = () => ({
    include_keywords: includeKw
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean),
    exclude_keywords: excludeKw
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean),
  });

  const reload = useCallback(async () => {
    const seq = ++loadSeq.current;
    if (!projectId) {
      setMan(null);
      return;
    }
    try {
      const m = await api.getLiteratureManifest(projectId);
      if (loadSeq.current !== seq) return;
      setMan(m);
      setError(null);
    } catch (e) {
      if (loadSeq.current !== seq) return;
      setError(formatApiError(e));
    }
    // W6-2: 预设与规则版本（fail-open，不阻塞主面板）
    try {
      const p = await api.getScreeningPresets();
      if (loadSeq.current !== seq) return;
      setPresets(p.presets.map((x) => ({ name: x.name, title: x.title })));
    } catch {
      /* ignore */
    }
    try {
      const rv = await api.getScreeningRuleVersions(projectId);
      if (loadSeq.current !== seq) return;
      setVersions(rv.versions);
      setHistory(rv.history.slice(-8).reverse());
      setCurrentRuleName(rv.current_rule_name ?? null);
      setRollbackVersion(""); // 刷新后复位回滚选择
    } catch {
      /* ignore */
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // F-4: 每轮渲染同步最新引用，供 run()/动作回调使用（幂等赋值，StrictMode 安全）。
  reloadRef.current = reload;
  projectIdRef.current = projectId;

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
      // F-4: 动作耗时中 project 可能已切换 —— 用最新 reload（当前 projectId）刷新，
      // 旧 project 的 reload 闭包不再直接调用，避免旧数据覆盖新面板。
      await reloadRef.current();
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
          {/* W6-2: 预设选择器 */}
          <label className="flex gap-1 items-center text-slate-400">
            预设
            <select
              className="flex-1 bg-ink border border-edge rounded px-1 py-0.5 text-slate-200"
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
              data-testid="literature-screen-preset"
            >
              <option value="">（无，使用下方关键词）</option>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
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
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              disabled={!!busy}
              className="px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-200 disabled:opacity-40"
              data-testid="literature-screen-btn"
              onClick={() =>
                run("screen", async () => {
                  const res = await api.screenLiteratureManifest({
                    project_id: projectId,
                    apply: true,
                    preset: preset || undefined,
                    rule_name: ruleName.trim() || undefined,
                    criteria: kwCriteria(),
                  });
                  // W6-2: 大 manifest 走后台 job，等流结束再刷新
                  const taskId = res.task_id as string | undefined;
                  if (res.async && taskId) {
                    await awaitTaskStream(taskId);
                  }
                })
              }
            >
              运行筛选
            </button>
            {/* W6-2: 命名规则版本保存 */}
            <input
              className="bg-ink border border-edge rounded px-1 py-0.5 text-slate-200 w-28"
              placeholder="规则版本名"
              value={ruleName}
              onChange={(e) => setRuleName(e.target.value)}
              data-testid="literature-rule-name"
            />
            <button
              type="button"
              disabled={!!busy || !ruleName.trim()}
              className="px-1.5 py-0.5 rounded border border-edge text-slate-300 disabled:opacity-40"
              data-testid="literature-rule-save-btn"
              onClick={() =>
                run("save-rule", () =>
                  api.saveScreeningRuleVersion({
                    project_id: projectId,
                    name: ruleName.trim(),
                    criteria: kwCriteria(),
                    created_by: "hub",
                  }),
                )
              }
            >
              保存版本
            </button>
            <button
              type="button"
              disabled={!!busy}
              className="px-1.5 py-0.5 rounded border border-amber-500/40 text-amber-200 disabled:opacity-40"
              data-testid="literature-eval-btn"
              onClick={() =>
                run("evaluate", async () => {
                  const pid = projectId;
                  const r = await api.evaluateScreening({
                    project_id: pid,
                    criteria: kwCriteria(),
                  });
                  // F-4: 评估耗时中 project 已切换时，丢弃旧 project 的评估结果。
                  if (projectIdRef.current !== pid) return;
                  setEvalRes(r);
                })
              }
            >
              效果评估
            </button>
          </div>
          {/* W6-2: 规则版本下拉 + 回滚 */}
          {versions.length > 0 && (
            <div
              className="flex gap-1 items-center text-slate-400"
              data-testid="literature-rule-versions"
            >
              <span>
                规则版本{currentRuleName ? `（当前 ${currentRuleName}）` : ""}
              </span>
              <select
                className="flex-1 bg-ink border border-edge rounded px-1 py-0.5 text-slate-200"
                id="literature-rule-version-select"
                data-testid="literature-rule-version-select"
                value={rollbackVersion}
                onChange={(e) => setRollbackVersion(e.target.value)}
              >
                <option value="" disabled>
                  选择历史版本回滚…
                </option>
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    {v.name} · {v.version.slice(0, 8)}
                    {v.changelog ? ` · ${v.changelog.slice(0, 20)}` : ""}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={!!busy || !rollbackVersion}
                className="px-1.5 py-0.5 rounded border border-edge text-slate-300 disabled:opacity-40"
                data-testid="literature-rule-rollback-btn"
                onClick={() => {
                  if (!rollbackVersion) return;
                  void run("rollback", () =>
                    api.rollbackScreeningRuleVersion({
                      project_id: projectId,
                      version: rollbackVersion,
                      actor: "hub",
                    }),
                  );
                }}
              >
                回滚
              </button>
            </div>
          )}
          {/* W6-2: 变更历史时间线（只读） */}
          {history.length > 0 && (
            <ul
              className="text-slate-500 space-y-0.5"
              data-testid="literature-rule-history"
            >
              {history.map((h, i) => (
                <li key={`${h.at}-${i}`}>
                  {new Date(h.at * 1000).toLocaleString("zh-CN", {
                    hour12: false,
                  })}{" "}
                  {h.actor} {h.action === "rolled_back" ? "回滚到" : "保存"}{" "}
                  {h.name}
                  {h.changelog ? `：${h.changelog.slice(0, 40)}` : ""}
                </li>
              ))}
            </ul>
          )}
          {/* W6-2: 评估结果 */}
          {evalRes && (
            <div
              className="text-slate-400 space-y-0.5"
              data-testid="literature-eval-result"
            >
              {evalRes.evaluated ? (
                <>
                  <p>
                    人工标注 {evalRes.labeled_count} 条 · P=
                    {evalRes.metrics?.precision.toFixed(2)} R=
                    {evalRes.metrics?.recall.toFixed(2)} F1=
                    {evalRes.metrics?.f1.toFixed(2)} · 混淆矩阵 TP
                    {evalRes.confusion?.tp}/FP{evalRes.confusion?.fp}/TN
                    {evalRes.confusion?.tn}/FN{evalRes.confusion?.fn}
                  </p>
                  {(evalRes.include_ablation ?? []).slice(0, 3).length > 0 && (
                    <p>
                      最伤 recall 的关键词：
                      {(evalRes.include_ablation ?? [])
                        .slice(0, 3)
                        .map(
                          (a) =>
                            `${a.keyword}(${a.recall_delta.toFixed(2)})`,
                        )
                        .join(" ")}
                    </p>
                  )}
                </>
              ) : (
                <p>
                  样本不足（人工标注 {evalRes.labeled_count ?? 0}{" "}
                  条，需 ≥5 条）无法评估
                </p>
              )}
            </div>
          )}
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
