import { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "../../api";
import { useStore } from "../../store";
import { useShallow } from "zustand/react/shallow";

type LibItem = Awaited<ReturnType<typeof api.getLiteratureLibrary>>["items"][number];
type Collection = Awaited<ReturnType<typeof api.getLiteratureLibrary>>["collections"][number];
type DupGroup = Awaited<ReturnType<typeof api.getLiteratureDuplicates>>["groups"][number];

const FLAG_ATTR = "literature_library_enabled";

function downloadText(filename: string, text: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Knowledge Hub — Literature Library catalog (Wave E-Lit). */
export default function HubLiteratureLibraryPane({ active }: { active: boolean }) {
  const { projectId, envFlagsRevision, openSettings } = useStore(
    useShallow((s) => ({
      projectId: s.activeProjectId,
      envFlagsRevision: s.envFlagsRevision,
      openSettings: s.openSettings,
    })),
  );

  const [flagOn, setFlagOn] = useState<boolean | null>(null);
  const [flagErr, setFlagErr] = useState<string | null>(null);
  const [items, setItems] = useState<LibItem[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [frozenIds, setFrozenIds] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [screening, setScreening] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [importText, setImportText] = useState("");
  const [dupGroups, setDupGroups] = useState<DupGroup[]>([]);
  const [exportScope, setExportScope] = useState<"library" | "frozen" | "collection">(
    "library",
  );
  const [draft, setDraft] = useState<{
    title: string;
    doi: string;
    year: string;
    authors: string;
    tags: string;
    notes: string;
    screening: string;
    collectionIds: string[];
  } | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    void api
      .getEnvFlags()
      .then((body) => {
        if (cancelled) return;
        const f = (body.flags ?? []).find((x) => x.attr === FLAG_ATTR);
        setFlagOn(Boolean(f?.value));
        setFlagErr(null);
      })
      .catch((e) => {
        if (!cancelled) {
          setFlagOn(null);
          setFlagErr(formatApiError(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [active, envFlagsRevision]);

  const refresh = useCallback(async () => {
    if (!projectId || flagOn !== true) return;
    setBusy(true);
    setErr(null);
    try {
      const lib = await api.getLiteratureLibrary(projectId, {
        q: q || undefined,
        tag: tag || undefined,
        collection_id: collectionId || undefined,
        screening: screening || undefined,
      });
      setItems(lib.items ?? []);
      setCollections(lib.collections ?? []);
      setFrozenIds(new Set(lib.frozen?.item_ids ?? []));
      setSelectedId((prev) => {
        if (prev && (lib.items ?? []).some((i) => i.id === prev)) return prev;
        return lib.items?.[0]?.id ?? null;
      });
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, flagOn, q, tag, collectionId, screening]);

  useEffect(() => {
    if (active && flagOn === true) void refresh();
  }, [active, flagOn, refresh]);

  const selected = useMemo(
    () => items.find((i) => i.id === selectedId) ?? null,
    [items, selectedId],
  );

  useEffect(() => {
    if (!selected) {
      setDraft(null);
      return;
    }
    setDraft({
      title: selected.title || "",
      doi: selected.doi || "",
      year: selected.year != null ? String(selected.year) : "",
      authors: (selected.authors || []).join("; "),
      tags: (selected.tags || []).join(", "),
      notes: selected.notes || "",
      screening: selected.screening || "unset",
      collectionIds: [...(selected.collection_ids || [])],
    });
  }, [selected]);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      await fn();
      setMsg(label);
      await refresh();
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  if (!projectId) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-500" data-testid="hub-library-pane">
        请先选择活动项目
      </div>
    );
  }

  if (flagOn !== true) {
    return (
      <div className="h-full flex flex-col gap-3 p-3" data-testid="hub-library-pane">
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-3 text-sm text-amber-100">
          <p className="font-medium">文献库未启用</p>
          <p className="text-xs text-amber-100/80 mt-1">
            开启 EnvFlag <code className="text-[11px]">{FLAG_ATTR}</code>（默认关，浸泡后再开）后可编目、导入
            DOI/ChemRxiv、查重与 BibTeX/RIS。
          </p>
          {flagErr && <p className="text-xs text-rose-300 mt-2">{flagErr}</p>}
          <button
            type="button"
            className="mt-3 text-xs px-2 py-1 rounded border border-amber-400/50 hover:bg-amber-500/20"
            data-testid="hub-library-flag-cta"
            onClick={() => openSettings("env", { focusEnvAttr: FLAG_ATTR })}
          >
            打开环境变量设置
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col gap-2 min-h-0" data-testid="hub-library-pane">
      <div className="flex flex-wrap gap-2 items-center shrink-0">
        <button
          type="button"
          disabled={busy}
          className="text-xs px-2 py-1 rounded border border-edge hover:border-accent/50"
          onClick={() => run("已捕获", () => api.captureLiteratureManifest({ project_id: projectId }))}
        >
          Capture
        </button>
        <button
          type="button"
          disabled={busy}
          className="text-xs px-2 py-1 rounded border border-edge hover:border-accent/50"
          onClick={() =>
            run("已冻结", () =>
              api.freezeLiteratureManifest({ project_id: projectId, actor: "hub-library" }),
            )
          }
        >
          Freeze
        </button>
        <button
          type="button"
          disabled={busy}
          className="text-xs px-2 py-1 rounded border border-edge hover:border-accent/50"
          onClick={() =>
            run("OA 补全文", () =>
              api.enrichLiteratureOa({
                project_id: projectId,
                scope: "missing_fulltext",
                limit: 20,
              }),
            )
          }
        >
          补全文
        </button>
        <select
          className="text-xs bg-panel border border-edge rounded px-2 py-1"
          value={exportScope}
          onChange={(e) =>
            setExportScope(e.target.value as "library" | "frozen" | "collection")
          }
          data-testid="hub-library-export-scope"
          title="导出范围"
        >
          <option value="library">导出：全部库</option>
          <option value="frozen">导出：仅冻结</option>
          <option value="collection">导出：当前集合</option>
        </select>
        <button
          type="button"
          disabled={busy || (exportScope === "collection" && !collectionId)}
          className="text-xs px-2 py-1 rounded border border-edge hover:border-accent/50"
          onClick={() =>
            run("导出 BibTeX", async () => {
              const text = await api.exportLiteratureBib(projectId, {
                scope: exportScope,
                collection_id:
                  exportScope === "collection" ? collectionId || undefined : undefined,
              });
              downloadText(`${projectId}-library.bib`, text, "application/x-bibtex");
            })
          }
        >
          导出 BibTeX
        </button>
        <button
          type="button"
          disabled={busy || (exportScope === "collection" && !collectionId)}
          className="text-xs px-2 py-1 rounded border border-edge hover:border-accent/50"
          onClick={() =>
            run("导出 RIS", async () => {
              const text = await api.exportLiteratureRis(projectId, {
                scope: exportScope,
                collection_id:
                  exportScope === "collection" ? collectionId || undefined : undefined,
              });
              downloadText(`${projectId}-library.ris`, text, "application/x-research-info-systems");
            })
          }
        >
          导出 RIS
        </button>
        <button
          type="button"
          disabled={busy}
          className="text-xs px-2 py-1 rounded border border-edge hover:border-accent/50"
          onClick={() =>
            run("查重", async () => {
              const d = await api.getLiteratureDuplicates(projectId);
              setDupGroups(d.groups ?? []);
            })
          }
        >
          查重
        </button>
        <span className="text-[10px] text-slate-500 ml-auto">
          {items.length} 条{busy ? " · …" : ""}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 shrink-0">
        <textarea
          className="w-full text-xs bg-panel border border-edge rounded px-2 py-1 min-h-[56px]"
          placeholder="粘贴 DOI / ChemRxiv 链接或 chemrxiv:<uuid>（不支持 arXiv）"
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          data-testid="hub-library-import-text"
        />
        <div className="flex flex-col gap-1">
          <button
            type="button"
            disabled={busy || !importText.trim()}
            className="text-xs px-2 py-1 rounded border border-accent/40 bg-accent/10"
            data-testid="hub-library-import-ids"
            onClick={() =>
              run("导入完成", async () => {
                const r = await api.importLiteratureIds({
                  project_id: projectId,
                  text: importText,
                });
                setMsg(
                  `导入 +${r.added.length} / skip ${r.skipped.length} / fail ${r.failures.length}`,
                );
                setImportText("");
              })
            }
          >
            导入 DOI / ChemRxiv
          </button>
          <button
            type="button"
            disabled={busy || !importText.trim()}
            className="text-xs px-2 py-1 rounded border border-edge"
            onClick={() =>
              run("BibTeX 导入", async () => {
                const r = await api.importLiteratureCitation({
                  project_id: projectId,
                  format: "bibtex",
                  text: importText,
                });
                setMsg(
                  `BibTeX +${r.added.length} / skip ${r.skipped.length} / fail ${r.failures.length}`,
                );
                setImportText("");
              })
            }
          >
            导入为 BibTeX
          </button>
          <button
            type="button"
            disabled={busy || !importText.trim()}
            className="text-xs px-2 py-1 rounded border border-edge"
            onClick={() =>
              run("RIS 导入", async () => {
                const r = await api.importLiteratureCitation({
                  project_id: projectId,
                  format: "ris",
                  text: importText,
                });
                setMsg(
                  `RIS +${r.added.length} / skip ${r.skipped.length} / fail ${r.failures.length}`,
                );
                setImportText("");
              })
            }
          >
            导入为 RIS
          </button>
        </div>
      </div>

      {dupGroups.length > 0 && (
        <div className="rounded border border-edge p-2 text-xs shrink-0 max-h-28 overflow-auto">
          <div className="text-slate-400 mb-1">重复组 {dupGroups.length}</div>
          {dupGroups.map((g) => (
            <div key={g.id} className="flex items-center gap-2 py-0.5">
              <span className="truncate flex-1">{g.title}</span>
              <span className="text-[10px] text-slate-500">{g.match}</span>
              <button
                type="button"
                className="text-[10px] px-1.5 py-0.5 border border-edge rounded"
                onClick={() =>
                  void run("已合并", async () => {
                    await api.mergeLiteratureItems({
                      project_id: projectId,
                      item_ids: g.item_ids,
                    });
                    setDupGroups((prev) => prev.filter((x) => x.id !== g.id));
                  })
                }
              >
                合并
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2 shrink-0">
        <input
          className="text-xs bg-panel border border-edge rounded px-2 py-1 flex-1 min-w-[120px]"
          placeholder="搜索标题 / DOI / 标签…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          data-testid="hub-library-search"
        />
        <input
          className="text-xs bg-panel border border-edge rounded px-2 py-1 w-28"
          placeholder="标签"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
        />
        <select
          className="text-xs bg-panel border border-edge rounded px-2 py-1"
          value={collectionId}
          onChange={(e) => setCollectionId(e.target.value)}
        >
          <option value="">全部集合</option>
          {collections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <select
          className="text-xs bg-panel border border-edge rounded px-2 py-1"
          value={screening}
          onChange={(e) => setScreening(e.target.value)}
        >
          <option value="">筛选态</option>
          <option value="match">match</option>
          <option value="no_match">no_match</option>
          <option value="uncertain">uncertain</option>
          <option value="unset">unset</option>
        </select>
        <button
          type="button"
          className="text-xs px-2 py-1 rounded border border-edge"
          onClick={() =>
            run("集合已创建", async () => {
              const name = window.prompt("集合名称");
              if (!name?.trim()) return;
              await api.createLiteratureCollection({
                project_id: projectId,
                name: name.trim(),
              });
            })
          }
        >
          + 集合
        </button>
      </div>

      {(msg || err) && (
        <div className={`text-[11px] shrink-0 ${err ? "text-rose-300" : "text-emerald-300/90"}`}>
          {err || msg}
        </div>
      )}

      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-2 gap-2">
        <ul className="overflow-auto border border-edge rounded divide-y divide-edge/60">
          {items.map((it) => {
            const activeRow = it.id === selectedId;
            const frozen = frozenIds.has(it.id);
            return (
              <li key={it.id}>
                <button
                  type="button"
                  className={`w-full text-left px-2 py-1.5 text-xs ${
                    activeRow ? "bg-accent/15" : "hover:bg-panel/80"
                  }`}
                  onClick={() => setSelectedId(it.id)}
                  data-testid={`hub-library-item-${it.id}`}
                >
                  <div className="flex items-center gap-1">
                    <span className="font-medium text-slate-100 truncate flex-1">
                      {it.title || it.id}
                    </span>
                    {frozen && <span className="text-amber-300 text-[10px]">❄</span>}
                  </div>
                  <div className="text-[10px] text-slate-500 truncate">
                    {[it.year, it.doi || it.chemrxiv_id, it.screening]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </button>
              </li>
            );
          })}
          {items.length === 0 && (
            <li className="px-2 py-4 text-xs text-slate-500 text-center">
              暂无条目 — 先 Capture 或导入 DOI/ChemRxiv
            </li>
          )}
        </ul>

        <div className="overflow-auto border border-edge rounded p-2 text-xs space-y-2">
          {!selected || !draft ? (
            <p className="text-slate-500">选择左侧条目编辑元数据</p>
          ) : (
            <>
              <label className="block space-y-0.5">
                <span className="text-slate-500">标题</span>
                <input
                  className="w-full bg-panel border border-edge rounded px-2 py-1"
                  value={draft.title}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
              </label>
              <label className="block space-y-0.5">
                <span className="text-slate-500">DOI</span>
                <input
                  className="w-full bg-panel border border-edge rounded px-2 py-1"
                  value={draft.doi}
                  onChange={(e) => setDraft({ ...draft, doi: e.target.value })}
                />
              </label>
              <label className="block space-y-0.5">
                <span className="text-slate-500">年份</span>
                <input
                  className="w-full bg-panel border border-edge rounded px-2 py-1"
                  value={draft.year}
                  onChange={(e) => setDraft({ ...draft, year: e.target.value })}
                />
              </label>
              <label className="block space-y-0.5">
                <span className="text-slate-500">作者（; 分隔）</span>
                <input
                  className="w-full bg-panel border border-edge rounded px-2 py-1"
                  value={draft.authors}
                  onChange={(e) => setDraft({ ...draft, authors: e.target.value })}
                />
              </label>
              <label className="block space-y-0.5">
                <span className="text-slate-500">标签（, 分隔）</span>
                <input
                  className="w-full bg-panel border border-edge rounded px-2 py-1"
                  value={draft.tags}
                  onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
                />
              </label>
              <label className="block space-y-0.5">
                <span className="text-slate-500">笔记</span>
                <textarea
                  className="w-full bg-panel border border-edge rounded px-2 py-1 min-h-[64px]"
                  value={draft.notes}
                  onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
                />
              </label>
              <label className="block space-y-0.5">
                <span className="text-slate-500">筛选</span>
                <select
                  className="w-full bg-panel border border-edge rounded px-2 py-1"
                  value={draft.screening}
                  onChange={(e) => setDraft({ ...draft, screening: e.target.value })}
                >
                  <option value="unset">unset</option>
                  <option value="match">match</option>
                  <option value="no_match">no_match</option>
                  <option value="uncertain">uncertain</option>
                </select>
              </label>
              <fieldset className="space-y-1" data-testid="hub-library-collections">
                <legend className="text-slate-500">所属集合</legend>
                {collections.length === 0 ? (
                  <p className="text-[10px] text-slate-600">暂无集合 — 先点「+ 集合」</p>
                ) : (
                  collections.map((c) => {
                    const checked = draft.collectionIds.includes(c.id);
                    return (
                      <label
                        key={c.id}
                        className="flex items-center gap-1.5 text-[11px] text-slate-300"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => {
                            setDraft({
                              ...draft,
                              collectionIds: checked
                                ? draft.collectionIds.filter((id) => id !== c.id)
                                : [...draft.collectionIds, c.id],
                            });
                          }}
                        />
                        <span className="truncate">{c.name}</span>
                      </label>
                    );
                  })
                )}
              </fieldset>
              <button
                type="button"
                disabled={busy}
                className="text-xs px-2 py-1 rounded border border-accent/50 bg-accent/10"
                data-testid="hub-library-save"
                onClick={() =>
                  run("已保存", () =>
                    api.patchLiteratureItem(selected.id, {
                      project_id: projectId,
                      title: draft.title,
                      doi: draft.doi || null,
                      year: draft.year ? Number(draft.year) : null,
                      authors: draft.authors
                        .split(";")
                        .map((s) => s.trim())
                        .filter(Boolean),
                      tags: draft.tags
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                      notes: draft.notes,
                      screening: draft.screening,
                      collection_ids: draft.collectionIds,
                    }),
                  )
                }
              >
                保存
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
