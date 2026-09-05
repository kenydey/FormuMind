/**
 * 材料库面板(2026-09-05, A2+A3 缺口补齐)。
 *
 * - 文本: 材料列表 / 搜索(q/role/availability 过滤, GET /api/materials)
 * - 新建/编辑: 与后端 MaterialSpec 对齐的表单(upsert)
 * - 供应状态: in_stock / restricted / discontinued 切换(驱动替代推荐)
 * - 结构: SMARTS 子结构搜索 + SMILES → Murcko 骨架替代候选(GET /api/chemical/*)
 * - 批量: 触发 enrich-materials 补全缺省 SMILES/摩尔质量
 */
import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import { api, type ChemicalHit, type MaterialView } from "../api";

const ROLES = ["", "resin", "additive", "inhibitor", "solvent", "crosslinker", "surfactant", "catalyst"];
const AVAIL_LABEL: Record<string, { text: string; cls: string }> = {
  in_stock: { text: "在库", cls: "border-emerald-500/40 text-emerald-400 bg-emerald-500/10" },
  restricted: { text: "受限", cls: "border-amber-500/40 text-amber-400 bg-amber-500/10" },
  discontinued: { text: "停产", cls: "border-rose-500/40 text-rose-400 bg-rose-500/10" },
};

type Tab = "library" | "structure";
type Mode = "closed" | "create" | "edit";

interface SpecDraft {
  name: string;
  role: string;
  zh_name?: string;
  cas_no?: string;
  formula?: string;
  smiles?: string;
  molar_mass?: string;
  price_cny_per_kg?: string;
  voc_contrib?: string;
  density_gcm3?: string;
}

const EMPTY: SpecDraft = {
  name: "",
  role: "additive",
  zh_name: "",
  cas_no: "",
  formula: "",
  smiles: "",
  molar_mass: "",
  price_cny_per_kg: "",
  voc_contrib: "",
  density_gcm3: "",
};

export default function MaterialsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("library");
  const [materials, setMaterials] = useState<MaterialView[]>([]);
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [avail, setAvail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("closed");
  const [editingName, setEditingName] = useState<string | null>(null);
  const [draft, setDraft] = useState<SpecDraft>(EMPTY);
  const [enriching, setEnriching] = useState(false);

  // ── 结构搜索状态 ──
  const [smarts, setSmarts] = useState("");
  const [smiles, setSmiles] = useState("");
  const [structBusy, setStructBusy] = useState(false);
  const [structHits, setStructHits] = useState<ChemicalHit[] | null>(null);
  const [structTitle, setStructTitle] = useState("");

  const load = useCallback(async (term = q, roleF = role, availF = avail) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.listMaterials({ q: term, role: roleF, availability: availF });
      setMaterials(res.materials ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [q, role, avail]);

  useEffect(() => {
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function startCreate() {
    setDraft(EMPTY);
    setEditingName(null);
    setMode("create");
  }

  function startEdit(m: MaterialView) {
    const s = (m.spec ?? {}) as Record<string, unknown>;
    setDraft({
      name: m.name,
      role: m.role,
      zh_name: (s.zh_name as string) ?? "",
      cas_no: (s.cas_no as string) ?? "",
      formula: (s.formula as string) ?? "",
      smiles: (s.smiles as string) ?? "",
      molar_mass: s.molar_mass != null ? String(s.molar_mass) : "",
      price_cny_per_kg: s.price_cny_per_kg != null ? String(s.price_cny_per_kg) : "",
      voc_contrib: s.voc_contrib != null ? String(s.voc_contrib) : "",
      density_gcm3: s.density_gcm3 != null ? String(s.density_gcm3) : "",
    });
    setEditingName(m.name);
    setMode("edit");
  }

  async function saveDraft() {
    if (!draft.name.trim() || !draft.role.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.upsertMaterial({
        name: draft.name.trim(),
        role: draft.role.trim(),
        zh_name: draft.zh_name?.trim() || null,
        cas_no: draft.cas_no?.trim() || null,
        formula: draft.formula?.trim() || null,
        smiles: draft.smiles?.trim() || null,
        molar_mass: draft.molar_mass ? Number(draft.molar_mass) : null,
        price_cny_per_kg: draft.price_cny_per_kg ? Number(draft.price_cny_per_kg) : null,
        voc_contrib: draft.voc_contrib ? Number(draft.voc_contrib) : null,
        density_gcm3: draft.density_gcm3 ? Number(draft.density_gcm3) : null,
      });
      setMode("closed");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleAvail(m: MaterialView) {
    const cur = m.availability ?? "in_stock";
    const next =
      cur === "in_stock" ? "restricted" : cur === "restricted" ? "discontinued" : "in_stock";
    try {
      await api.setMaterialAvailability(m.name, next);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function runEnrich() {
    setEnriching(true);
    setError(null);
    try {
      const res = await api.enrichMaterials();
      await load();
      setError(null);
      setEnrichMsg(`属性补全完成: ${(res as { enriched?: number }).enriched ?? "?"} 条更新`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setEnriching(false);
    }
  }
  const [enrichMsg, setEnrichMsg] = useState<string | null>(null);

  async function runStructureSearch() {
    const query = smarts.trim();
    if (!query) return;
    setStructBusy(true);
    setStructHits(null);
    setStructTitle("");
    try {
      if (query.startsWith("[")) {
        setStructHits(await api.substructureSearch(query));
        setStructTitle(`SMARTS 子结构匹配: ${query}`);
      } else if (query.includes("(")) {
        // SMILES 也可被子结构接口拒绝 —— 统一走骨架替代
        setStructHits(await api.scaffoldSubstitutes(query));
        setStructTitle(`Murcko 骨架替代候选(SMILES): ${query}`);
      } else {
        setStructHits(await api.substructureSearch(query));
        setStructTitle(`SMARTS 子结构匹配: ${query}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStructBusy(false);
    }
  }

  const [showHits, setShowHits] = useState(false);
  useEffect(() => {
    if (structHits && structHits.length) setShowHits(true);
  }, [structHits]);

  const specOf = (m: MaterialView) => (m.spec ?? {}) as Record<string, unknown>;
  const fmt = (v: unknown) => (v === undefined || v === null ? "" : String(v));

  return (
    <Modal title="🧪 材料库 · Materials" open={open} onClose={onClose} size="xl" testId="modal-materials">
      <div className="flex gap-2 mb-3">
        <button
          type="button"
          onClick={() => setTab("library")}
          className={`text-xs rounded-full px-3 py-1 border ${
            tab === "library" ? "border-accent/50 bg-accent/10 text-accent" : "border-edge text-slate-400"
          }`}
        >
          材料列表
        </button>
        <button
          type="button"
          onClick={() => setTab("structure")}
          className={`text-xs rounded-full px-3 py-1 border ${
            tab === "structure" ? "border-accent/50 bg-accent/10 text-accent" : "border-edge text-slate-400"
          }`}
        >
          结构搜索
        </button>
        <button
          type="button"
          disabled={enriching}
          onClick={() => void runEnrich()}
          className="ml-auto text-[10px] border border-edge rounded-full px-3 py-1 text-slate-400 hover:border-accent/40 hover:text-accent disabled:opacity-50"
          title="为缺失 SMILES/摩尔质量的材料批量补全属性(PubChem + 网页兜底)"
        >
          {enriching ? "补全中…" : "⚡ 批量补全属性"}
        </button>
      </div>

      {enrichMsg && <div className="text-[11px] text-emerald-400 mb-2">{enrichMsg}</div>}
      {error && (
        <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded p-2 mb-2">{error}</div>
      )}

      {tab === "library" ? (
        <div className="space-y-3">
          <div className="flex gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void load();
              }}
              placeholder="搜索名称 / CAS / 中文名…"
              className="flex-1 bg-ink border border-edge rounded px-3 py-1.5 text-sm"
            />
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="bg-ink border border-edge rounded px-2 py-1.5 text-xs text-slate-300"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r || "全部角色"}
                </option>
              ))}
            </select>
            <select
              value={avail}
              onChange={(e) => setAvail(e.target.value)}
              className="bg-ink border border-edge rounded px-2 py-1.5 text-xs text-slate-300"
            >
              <option value="">全部状态</option>
              <option value="in_stock">在库</option>
              <option value="restricted">受限</option>
              <option value="discontinued">停产</option>
            </select>
            <button
              type="button"
              onClick={() => void load()}
              disabled={busy}
              className="bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 text-sm disabled:opacity-40"
            >
              {busy ? "…" : "查询"}
            </button>
            <button
              type="button"
              onClick={startCreate}
              className="border border-accent/50 text-accent rounded px-3 text-sm hover:bg-accent/10"
            >
              + 新材料
            </button>
          </div>

          <div className="max-h-[46vh] overflow-auto border border-edge rounded">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-panel text-slate-500">
                <tr className="border-b border-edge">
                  <th className="text-left px-2 py-1.5 font-medium">名称</th>
                  <th className="text-left px-2 py-1.5 font-medium">中文名</th>
                  <th className="text-left px-2 py-1.5 font-medium">角色</th>
                  <th className="text-left px-2 py-1.5 font-medium">CAS</th>
                  <th className="text-left px-2 py-1.5 font-medium">分子式</th>
                  <th className="text-left px-2 py-1.5 font-medium">M (g/mol)</th>
                  <th className="text-left px-2 py-1.5 font-medium">价格 ¥/kg</th>
                  <th className="text-left px-2 py-1.5 font-medium">状态</th>
                  <th className="px-2 py-1.5" />
                </tr>
              </thead>
              <tbody>
                {materials.map((m) => {
                  const s = specOf(m);
                  const al = AVAIL_LABEL[m.availability ?? "in_stock"] ?? AVAIL_LABEL.in_stock;
                  return (
                    <tr key={m.name} className="border-b border-edge/50 hover:bg-ink/60">
                      <td className="px-2 py-1.5 text-slate-200">
                        {m.name}
                        {(s.smiles as string) && (
                          <span className="ml-1 text-[9px] text-slate-600 font-mono">SMILES ✓</span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-slate-400">{fmt(s.zh_name)}</td>
                      <td className="px-2 py-1.5 text-slate-400">{m.role}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-400">{fmt(s.cas_no)}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-400">{fmt(s.formula)}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-400">{fmt(s.molar_mass)}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-400">
                        {s.price_cny_per_kg != null ? `¥${s.price_cny_per_kg}` : ""}
                      </td>
                      <td className="px-2 py-1.5">
                        <button
                          type="button"
                          onClick={() => void toggleAvail(m)}
                          title="点击切换 在库 → 受限 → 停产"
                          className={`text-[9px] rounded-full border px-1.5 py-0.5 ${al.cls}`}
                        >
                          {al.text}
                        </button>
                      </td>
                      <td className="px-2 py-1.5 text-right">
                        <button
                          type="button"
                          onClick={() => startEdit(m)}
                          className="text-[10px] text-slate-500 hover:text-accent"
                        >
                          编辑
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!busy && materials.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-2 py-6 text-center text-slate-500">
                      无匹配材料 — 点「+ 新材料」录入, 或切换到「结构搜索」页签按结构发现候选
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-slate-500">
            共 {materials.length} 条 · 材料状态会驱动配方替代推荐(替代时优先在库)。
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex gap-2">
            <input
              value={smarts}
              onChange={(e) => setSmarts(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && smarts.trim()) void runStructureSearch();
              }}
              placeholder="SMARTS 子结构, 如 C(=O)O (羧基) / c1ccccc1 (苯环) / [Si](C)(C)(C)C"
              className="flex-1 bg-ink border border-edge rounded px-3 py-2 text-sm font-mono"
            />
            <button
              type="button"
              disabled={structBusy || !smarts.trim()}
              onClick={() => void runStructureSearch()}
              className="shrink-0 bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 text-sm disabled:opacity-40"
            >
              {structBusy ? "检索中…" : "结构检索"}
            </button>
          </div>
          <div className="flex gap-2 items-center">
            <input
              value={smiles}
              onChange={(e) => setSmiles(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && smiles.trim()) {
                  setSmarts(smiles);
                  void (async () => {
                    setStructBusy(true);
                    setStructHits(null);
                    try {
                      setStructHits(await api.scaffoldSubstitutes(smiles.trim()));
                      setStructTitle(`Murcko 骨架替代候选(SMILES): ${smiles.trim()}`);
                    } catch (err) {
                      setError(err instanceof Error ? err.message : String(err));
                    } finally {
                      setStructBusy(false);
                    }
                  })();
                }
              }}
              placeholder="或输入目标材料 SMILES 找同骨架可替换候选…"
              className="flex-1 bg-ink border border-edge rounded px-3 py-2 text-sm font-mono"
            />
            <button
              type="button"
              disabled={structBusy || !smiles.trim()}
              onClick={() => {
                setSmarts(smiles);
                void (async () => {
                  setStructBusy(true);
                  setStructHits(null);
                  try {
                    setStructHits(await api.scaffoldSubstitutes(smiles.trim()));
                    setStructTitle(`Murcko 骨架替代候选(SMILES): ${smiles.trim()}`);
                  } catch (err) {
                    setError(err instanceof Error ? err.message : String(err));
                  } finally {
                    setStructBusy(false);
                  }
                })();
              }}
              className="shrink-0 border border-accent2/50 text-accent2 rounded px-3 text-sm hover:bg-accent2/10 disabled:opacity-40"
            >
              骨架替代
            </button>
          </div>
          <p className="text-[10px] text-slate-500">
            子结构: 筛选材料库中含指定官能团/环的材料(SMARTS)。骨架替代: 同 Murcko 骨架 = drop-in 候选,
            侧链差异是主要变点 — 适合在库材料停产/受限时的替换决策。
          </p>
          {structBusy && <div className="text-xs text-slate-400 py-4 text-center">结构检索中…</div>}
          {!structBusy && structTitle && (
            <div className="text-xs text-slate-300 mb-1">{structTitle} · {structHits?.length ?? 0} 条</div>
          )}
          {structHits && (
            <div className="max-h-[38vh] overflow-auto border border-edge rounded">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-panel text-slate-500">
                  <tr className="border-b border-edge">
                    <th className="text-left px-2 py-1.5 font-medium">材料</th>
                    <th className="text-left px-2 py-1.5 font-medium">角色</th>
                    <th className="text-left px-2 py-1.5 font-medium">CAS / 分子式</th>
                    <th className="text-left px-2 py-1.5 font-medium">相似度/说明</th>
                    <th className="text-left px-2 py-1.5 font-medium">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {structHits.map((h, i) => {
                    const al = AVAIL_LABEL[h.availability ?? "in_stock"] ?? AVAIL_LABEL.in_stock;
                    return (
                      <tr key={`${h.name}-${i}`} className="border-b border-edge/50">
                        <td className="px-2 py-1.5 text-slate-200">{h.name}</td>
                        <td className="px-2 py-1.5 text-slate-400">{h.role}</td>
                        <td className="px-2 py-1.5 font-mono text-slate-400">
                          {h.cas_no ?? ""} {h.formula ? `· ${h.formula}` : ""}
                        </td>
                        <td className="px-2 py-1.5 text-slate-400">
                          {h.similarity != null ? `≈${Math.round(h.similarity * 100)}%` : ""}
                          {h.reason ? ` ${h.reason}` : ""}
                        </td>
                        <td className="px-2 py-1.5">
                          <span className={`text-[9px] rounded-full border px-1.5 py-0.5 ${al.cls}`}>{al.text}</span>
                        </td>
                      </tr>
                    );
                  })}
                  {structHits.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-2 py-6 text-center text-slate-500">
                        无匹配 — 尝试更简单的 SMARTS(如 C(=O)O)或确认 SMILES 合法
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          {showHits && !structHits && <div />}
        </div>
      )}

      {(mode === "create" || mode === "edit") && (
        <Modal
          title={mode === "create" ? "录入新材料" : `编辑材料: ${editingName ?? ""}`}
          open
          onClose={() => setMode("closed")}
          nested
          size="md"
          testId="modal-material-edit"
        >
          <div className="space-y-2">
            <div className="flex gap-2">
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">名称 *</span>
                <input
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm"
                />
              </label>
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">角色 *</span>
                <input
                  value={draft.role}
                  onChange={(e) => setDraft({ ...draft, role: e.target.value })}
                  placeholder="resin / additive / inhibitor…"
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm"
                />
              </label>
            </div>
            <div className="flex gap-2">
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">中文名</span>
                <input
                  value={draft.zh_name ?? ""}
                  onChange={(e) => setDraft({ ...draft, zh_name: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm"
                />
              </label>
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">CAS No.</span>
                <input
                  value={draft.cas_no ?? ""}
                  onChange={(e) => setDraft({ ...draft, cas_no: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                />
              </label>
            </div>
            <div className="flex gap-2">
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">分子式</span>
                <input
                  value={draft.formula ?? ""}
                  onChange={(e) => setDraft({ ...draft, formula: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                />
              </label>
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">SMILES</span>
                <input
                  value={draft.smiles ?? ""}
                  onChange={(e) => setDraft({ ...draft, smiles: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                />
              </label>
            </div>
            <div className="flex gap-2">
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">摩尔质量 g/mol</span>
                <input
                  value={draft.molar_mass ?? ""}
                  onChange={(e) => setDraft({ ...draft, molar_mass: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                />
              </label>
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">价格 ¥/kg</span>
                <input
                  value={draft.price_cny_per_kg ?? ""}
                  onChange={(e) => setDraft({ ...draft, price_cny_per_kg: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                />
              </label>
            </div>
            <div className="flex gap-2">
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">VOC 贡献 g/L</span>
                <input
                  value={draft.voc_contrib ?? ""}
                  onChange={(e) => setDraft({ ...draft, voc_contrib: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                />
              </label>
              <label className="flex-1 block">
                <span className="text-[10px] text-slate-500">密度 g/cm³</span>
                <input
                  value={draft.density_gcm3 ?? ""}
                  onChange={(e) => setDraft({ ...draft, density_gcm3: e.target.value })}
                  className="w-full bg-ink border border-edge rounded px-2 py-1.5 text-sm font-mono"
                />
              </label>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setMode("closed")}
                className="border border-edge rounded px-4 py-1.5 text-sm text-slate-400"
              >
                取消
              </button>
              <button
                type="button"
                disabled={busy || !draft.name.trim() || !draft.role.trim()}
                onClick={() => void saveDraft()}
                className="bg-accent text-ink font-semibold rounded px-4 py-1.5 text-sm disabled:opacity-40"
              >
                {busy ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </Modal>
  );
}
