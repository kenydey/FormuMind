import { useEffect, useMemo, useState } from "react";
import {
  api,
  awaitTaskStream,
  type ChemToolsStatus,
  type DependencyInfo,
  type DependencyInstallResult,
  type KBStats,
} from "../api";

const CHEMTOOL_LABELS: Record<string, string> = {
  name_to_smiles: "名称→SMILES",
  name_to_cas: "名称→CAS",
  func_groups: "官能团识别",
  mol_similarity: "分子相似度",
  patent_check: "分子专利预筛",
  controlled_check: "管制品筛查",
  explosive_check: "爆炸性筛查",
  web_search: "化学网络检索",
};

function ChemToolsCard({ status }: { status: ChemToolsStatus | null }) {
  if (!status) return null;
  const caps = Object.entries(status.capabilities || {});
  const okCount = caps.filter(([, c]) => c.available).length;
  return (
    <div className="border border-edge/60 rounded p-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
        化学工具网关 · {okCount}/{caps.length} 可用
        {!status.enabled && <span className="text-amber-400 ml-1">（已禁用）</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        {caps.map(([key, cap]) => (
          <span
            key={key}
            title={cap.hint ?? undefined}
            className={`text-[10px] px-1.5 py-0.5 rounded border ${
              cap.available
                ? "border-teal-500/40 bg-teal-500/10 text-teal-300"
                : "border-edge bg-ink/60 text-slate-500"
            }`}
          >
            {cap.available ? "●" : "○"} {CHEMTOOL_LABELS[key] ?? key}
          </span>
        ))}
      </div>
    </div>
  );
}

function KnowledgeBaseCard({
  stats,
  onReindex,
  reindexing,
}: {
  stats: KBStats | null;
  onReindex: () => void;
  reindexing: boolean;
}) {
  if (!stats) return null;
  return (
    <div className="border border-edge/60 rounded p-2">
      <div className="flex items-center justify-between mb-1">
        <div className="text-[11px] uppercase tracking-wide text-slate-500">
          持久知识库
          {!stats.enabled && <span className="text-amber-400 ml-1">（已禁用）</span>}
        </div>
        {stats.enabled && (
          <button
            onClick={onReindex}
            disabled={reindexing}
            className="text-[10px] px-1.5 py-0.5 rounded border border-edge text-slate-400 hover:text-slate-200 hover:border-accent/50 disabled:opacity-50"
            title="对已存储的全部文档重建切块与向量索引"
          >
            {reindexing ? "重建中…" : "重建索引"}
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1 text-[10px]">
        <span className="px-1.5 py-0.5 rounded border border-edge bg-ink/60 text-slate-400">
          文档 {stats.sources}
        </span>
        <span className="px-1.5 py-0.5 rounded border border-edge bg-ink/60 text-slate-400">
          切块 {stats.chunks}
        </span>
        {/* Keyed on vector_mode, not embedding_available. The library being
            importable says nothing about whether any chunk actually carries a
            vector — the model download can fail and every embedding silently
            becomes NULL — so a badge driven by the import probe would show
            green over a corpus doing keyword matching. */}
        <span
          className={`px-1.5 py-0.5 rounded border ${
            stats.vector_mode === "semantic"
              ? "border-teal-500/40 bg-teal-500/10 text-teal-300"
              : stats.vector_mode === "degraded"
                ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
                : "border-edge bg-ink/60 text-slate-500"
          }`}
          title={
            stats.vector_hint ||
            (stats.vector_mode === "semantic"
              ? `检索为语义向量模式（${stats.rag_backend ?? "embedding"}）`
              : "尚无切块，入库后再看")
          }
        >
          {stats.vector_mode === "degraded" ? "⚠ " : ""}
          向量 {stats.embedded_chunks}/{stats.chunks}
        </span>
        {(stats.products ?? 0) > 0 && (
          <span
            className="px-1.5 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300"
            title="从文献/专利中自动识别的商业化学品牌号登记簿（牌号/供应商/通用名）"
          >
            商业产品 {stats.products}
          </span>
        )}
        {Object.entries(stats.sources_by_kind || {}).map(([kind, n]) => (
          <span
            key={kind}
            className="px-1.5 py-0.5 rounded border border-edge bg-ink/60 text-slate-500"
          >
            {kind} ×{n}
          </span>
        ))}
      </div>
    </div>
  );
}

// Human labels for the extra groups, in display order.
const EXTRA_LABELS: Record<string, string> = {
  llm: "大模型供应商 · LLM",
  intel: "在线检索 · Retrieval",
  embedding: "语义向量 RAG · Embedding",
  colbert: "ColBERT 检索 · ColBERT",
  crag: "联邦检索 · CRAG",
  science: "科学计算 · Science",
  optimize: "寻优器 · Optimize",
  bo: "高斯过程寻优 · BoTorch",
  pydoe: "经典 DOE · pyDOE",
  baybe: "贝叶斯主动学习 · BayBE",
  color: "色差 · Color",
  file_ingest: "文件解析 · Ingest",
  // Without an entry here the group is filtered out of the UI entirely by
  // EXTRA_ORDER, so every parse_pro package was installable only from a shell.
  parse_pro: "版面感知解析 · Layout",
  export: "导出 · Export",
  notebooklm: "NotebookLM",
};
const EXTRA_ORDER = Object.keys(EXTRA_LABELS);

/**
 * KB/KG 维护诊断卡(B1/B2/B3, 2026-09-05): 完整性扫描(孤儿引用) /
 * 重建知识图谱 / Neo4j 图谱适配层状态。
 */
function KbDiagnosticsCard() {
  const [integrity, setIntegrity] = useState<import("../api").KbIntegrityResponse | null>(null);
  const [integBusy, setIntegBusy] = useState(false);
  const [rebuildBusy, setRebuildBusy] = useState(false);
  const [neo4j, setNeo4j] = useState<import("../api").Neo4jStats | null>(null);
  const [neoBusy, setNeoBusy] = useState(false);
  const [report, setReport] = useState<string | null>(null);

  async function runIntegrity() {
    setIntegBusy(true);
    setReport(null);
    try {
      setIntegrity(await api.kbIntegrity());
    } catch (e) {
      setReport(`完整性检查失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setIntegBusy(false);
    }
  }

  async function runRebuild() {
    if (!window.confirm("重建知识图谱将从全部已入库资料重新提取实体/关系, 耗时较长, 确定执行?")) return;
    setRebuildBusy(true);
    setReport(null);
    try {
      const r = await api.kgRebuild();
      setReport(
        `✓ 图谱重建完成: ${r.linked_sources} 源 / ${r.entities_upserted} 实体 / ${r.mentions_upserted} 提及 / ${r.links_created} 链接`
      );
    } catch (e) {
      setReport(`重建失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRebuildBusy(false);
    }
  }

  const [relBusy, setRelBusy] = useState(false);

  async function runRelationsRebuild() {
    const all = window.confirm(
      "补语义关系提取(实体已在库, 只重跑关系)。全库执行可能 10-60 分钟(LLM 逐句); 建议先对单条资料测试。确定全库执行?"
    );
    if (!all) return;
    setRelBusy(true);
    setReport(null);
    try {
      const r = await api.kgRelationsRebuild(undefined);
      setReport(`关系重建已后台启动 (task ${r.task_id.slice(0, 8)}…) — 可稍后刷新本卡查看产出`);
      setRelTaskId(r.task_id);
    } catch (e) {
      setReport(`启动失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRelBusy(false);
    }
  }
  const [relTaskId, setRelTaskId] = useState<string | null>(null);
  const [relStatus, setRelStatus] = useState<string | null>(null);

  async function refreshRelStatus() {
    if (!relTaskId) return;
    try {
      const t = await api.task(relTaskId);
      setRelStatus(`${t.state}: ${t.message ?? ""}`.slice(0, 120));
    } catch {
      setRelStatus("状态查询失败");
    }
  }

  async function runNeo4j() {
    setNeoBusy(true);
    setReport(null);
    try {
      const stats = await api.neo4jStats();
      setNeo4j(stats);
      setNeo4jOpen((stats.reachable !== false && stats.enabled !== false) || neo4jOpen);
      if (stats.reachable !== false) void searchNeo4j("");
    } catch (e) {
      setReport(`Neo4j 不可达: ${e instanceof Error ? e.message : String(e)}`);
      setNeo4j(null);
    } finally {
      setNeoBusy(false);
    }
  }

  const [neo4jOpen, setNeo4jOpen] = useState(false);
  const [neoQuery, setNeoQuery] = useState("");
  const [neoCompounds, setNeoCompounds] = useState<import("../api").Neo4jCompound[] | null>(null);
  const [neoFormulations, setNeoFormulations] = useState<import("../api").Neo4jFormulation[] | null>(null);
  const [neoBusyBrowse, setNeoBusyBrowse] = useState(false);
  const [neoSimilar, setNeoSimilar] = useState<Record<string, unknown[]>>({});
  const [neoFormComps, setNeoFormComps] = useState<Record<string, unknown[]>>({});

  async function searchNeo4j(q: string) {
    setNeoBusyBrowse(true);
    try {
      const [comps, forms] = await Promise.all([api.neo4jCompounds(q, 80), api.neo4jFormulations(50)]);
      setNeoCompounds(comps);
      setNeoFormulations(forms);
    } catch (e) {
      setReport(`Neo4j 浏览失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setNeoBusyBrowse(false);
    }
  }

  async function loadSimilar(uid: string) {
    if (neoSimilar[uid]) return;
    try {
      const rows = await api.neo4jCompoundSimilar(uid);
      setNeoSimilar((m) => ({ ...m, [uid]: rows }));
    } catch {
      /* 静默 */
    }
  }

  async function loadFormulationCompounds(uid: string) {
    if (neoFormComps[uid]) return;
    try {
      const rows = await api.neo4jFormulationCompounds(uid);
      setNeoFormComps((m) => ({ ...m, [uid]: rows }));
    } catch {
      /* 静默 */
    }
  }

  return (
    <div className="rounded border border-edge p-3">
      <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">
        KB / KG 维护 · 诊断
      </div>
      <div className="flex flex-wrap gap-2 mb-2">
        <button
          type="button"
          disabled={integBusy}
          onClick={() => void runIntegrity()}
          className="text-[10px] border border-edge rounded px-2 py-1 text-slate-300 hover:border-accent/40 hover:text-accent disabled:opacity-50"
        >
          {integBusy ? "扫描中…" : "🔍 KB 完整性扫描"}
        </button>
        <button
          type="button"
          disabled={rebuildBusy}
          onClick={() => void runRebuild()}
          className="text-[10px] border border-amber-500/40 rounded px-2 py-1 text-amber-400 hover:bg-amber-500/10 disabled:opacity-50"
        >
          {rebuildBusy ? "重建中…" : "♻ 重建知识图谱"}
        </button>
        <button
          type="button"
          disabled={relBusy}
          onClick={() => void runRelationsRebuild()}
          className="text-[10px] border border-edge rounded px-2 py-1 text-slate-300 hover:border-accent/40 hover:text-accent disabled:opacity-50"
          title="实体/提及已在库, 异步补语义关系(LLM 慢, 全库 10-60 分钟)"
        >
          {relBusy ? "启动中…" : "🕸 补语义关系"}
        </button>
        {relTaskId && (
          <button
            type="button"
            onClick={() => void refreshRelStatus()}
            className="text-[10px] border border-edge rounded px-2 py-1 text-slate-500 hover:text-slate-300"
            title={relTaskId}
          >
            {relStatus ? `状态: ${relStatus}` : "查状态"}
          </button>
        )}
        <button
          type="button"
          disabled={neoBusy}
          onClick={() => void runNeo4j()}
          className="text-[10px] border border-edge rounded px-2 py-1 text-slate-300 hover:border-accent/40 hover:text-accent disabled:opacity-50"
        >
          {neoBusy ? "连接中…" : "🕸 Neo4j 状态"}
        </button>
      </div>

      {integrity && (
        <div
          className={`text-[11px] rounded px-2 py-1.5 mb-1 border ${
            integrity.healthy
              ? "border-emerald-500/40 text-emerald-400"
              : "border-amber-500/40 text-amber-400"
          }`}
        >
          {integrity.healthy
            ? "✓ KB 无孤儿引用"
            : `⚠ ${integrity.total_orphans} 个孤儿引用${integrity.external_backend ? "（外部向量后端）" : ""}`}
        </div>
      )}
      {neo4j && (
        <div className="text-[11px] text-slate-400 rounded px-2 py-1.5 mb-1 border border-edge">
          Neo4j {neo4j.reachable === false ? "不可达" : `就绪 · ${neo4j.nodes ?? "?"} 节点 / ${neo4j.edges ?? "?"} 边`}
          {neo4j.compounds != null && ` · ${neo4j.compounds} 化合物`}
          {neo4j.formulations != null && ` · ${neo4j.formulations} 配方`}
          <span className="text-slate-600"> (适配层, 与 SQLite 图谱共存)</span>
        </div>
      )}
      {neo4j?.reachable !== false && neo4j?.enabled !== false && neo4jOpen && (
        <div className="border border-edge rounded p-2 mb-1 space-y-2">
          <div className="flex gap-2">
            <input
              value={neoQuery}
              onChange={(e) => setNeoQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void searchNeo4j(neoQuery.trim());
              }}
              placeholder="搜索化合物(名称/CAS/uid)…"
              className="flex-1 bg-ink border border-edge rounded px-2 py-1 text-[11px]"
            />
            <button
              type="button"
              disabled={neoBusyBrowse}
              onClick={() => void searchNeo4j(neoQuery.trim())}
              className="text-[10px] border border-accent/50 text-accent rounded px-2 py-1 disabled:opacity-50"
            >
              {neoBusyBrowse ? "…" : "搜索"}
            </button>
            <button
              type="button"
              onClick={() => setNeo4jOpen(false)}
              className="text-[10px] text-slate-500 rounded px-1"
              title="收起"
            >
              ✕
            </button>
          </div>
          <div className="max-h-56 overflow-auto space-y-1">
            {(neoCompounds ?? []).map((c) => (
              <div key={c.uid} className="border border-edge/60 rounded px-2 py-1">
                <div className="flex items-center gap-2 text-[11px]">
                  <span className="text-slate-200 truncate flex-1" title={c.uid}>
                    {c.name ?? c.uid}
                  </span>
                  {c.cas_number && <span className="font-mono text-slate-500">{c.cas_number}</span>}
                  {c.molecular_weight != null && (
                    <span className="font-mono text-slate-500">{c.molecular_weight} g/mol</span>
                  )}
                  <button
                    type="button"
                    onClick={() => void loadSimilar(c.uid)}
                    className="text-[9px] text-accent2 hover:underline shrink-0"
                    title="同配方共现化合物(图路径)"
                  >
                    相似▾
                  </button>
                </div>
                {c.smiles && <div className="text-[9px] font-mono text-slate-600 truncate">{c.smiles}</div>}
                {neoSimilar[c.uid] && (
                  <div className="mt-1 pl-2 border-l border-edge">
                    {(neoSimilar[c.uid] as Array<{ uid: string; name?: string; co_count?: number }>).map((s) => (
                      <div key={s.uid} className="text-[10px] text-slate-400 flex gap-1">
                        <span className="truncate flex-1">↳ {s.name ?? s.uid}</span>
                        {s.co_count != null && <span className="text-slate-600">×{s.co_count}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {neoCompounds && neoCompounds.length === 0 && (
              <div className="text-[10px] text-slate-500 py-1">无匹配化合物</div>
            )}
            {!neoCompounds && (
              <div className="text-[10px] text-slate-500 py-1">点「🕸 Neo4j 状态」加载图谱数据…</div>
            )}
          </div>
          {neoFormulations && neoFormulations.length > 0 && (
            <div className="border-t border-edge pt-1">
              <div className="text-[10px] text-slate-500 mb-1">配方(图库)</div>
              <div className="max-h-28 overflow-auto space-y-1">
                {neoFormulations.map((f) => (
                  <div key={f.uid} className="flex items-center gap-2 text-[10px]">
                    <button
                      type="button"
                      onClick={() => void loadFormulationCompounds(f.uid)}
                      className="text-slate-300 truncate flex-1 hover:text-accent text-left"
                      title={f.uid}
                    >
                      {f.name ?? f.uid}
                      {f.target_property ? ` · ${f.target_property}` : ""}
                    </button>
                    {neoFormComps[f.uid] && (
                      <span className="text-slate-600">{(neoFormComps[f.uid] as unknown[]).length} 组分</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {report && <div className="text-[11px] text-slate-300 rounded px-2 py-1.5 mb-1 border border-edge">{report}</div>}
    </div>
  );
}

export default function DependencyManager({ reloadKey = 0 }: { reloadKey?: number }) {
  const [deps, setDeps] = useState<DependencyInfo[]>([]);
  const [coreMissing, setCoreMissing] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string>("");
  const [result, setResult] = useState<DependencyInstallResult | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chemTools, setChemTools] = useState<ChemToolsStatus | null>(null);
  const [kbStats, setKbStats] = useState<KBStats | null>(null);
  const [kbReindexing, setKbReindexing] = useState(false);

  async function refresh() {
    setLoading(true);
    setLoadError(null);
    try {
      const r = await api.listDependencies();
      setDeps(r.dependencies ?? []);
      setCoreMissing(r.online_core_missing ?? []);
    } catch (e) {
      setDeps([]);
      setCoreMissing([]);
      setLoadError(String(e));
    } finally {
      setLoading(false);
    }
    try {
      setChemTools(await api.chemicalTools());
    } catch {
      setChemTools(null);
    }
    try {
      setKbStats(await api.kbStats());
    } catch {
      setKbStats(null);
    }
  }

  async function reindexKb() {
    setKbReindexing(true);
    try {
      await api.kbReindex();
      setKbStats(await api.kbStats());
    } catch {
      /* stats card simply keeps the previous numbers */
    } finally {
      setKbReindexing(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [reloadKey]);

  const grouped = useMemo(() => {
    const map = new Map<string, DependencyInfo[]>();
    for (const d of deps) {
      if (!map.has(d.extra)) map.set(d.extra, []);
      map.get(d.extra)!.push(d);
    }
    return EXTRA_ORDER.filter((e) => map.has(e)).map((e) => ({
      extra: e,
      label: EXTRA_LABELS[e] ?? e,
      items: map.get(e)!,
    }));
  }, [deps]);

  function toggle(name: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  }

  async function run(names: string[], upgrade: boolean) {
    if (names.length === 0) return;
    setBusy(true);
    setResult(null);
    setProgress(`${upgrade ? "更新" : "安装"}中：${names.join(", ")} …`);
    try {
      const { task_id } = await api.installDependencies(names, upgrade);
      const final = await awaitTaskStream(task_id, (ev) =>
        setProgress(ev.message || "处理中…")
      );
      const res = (final.data as unknown as DependencyInstallResult) ?? {
        ok: false,
        summary: final.message,
      };
      setResult(res);
      setSelected(new Set());
      await refresh();
    } catch (e) {
      setResult({ ok: false, summary: String(e) });
    } finally {
      setBusy(false);
      setProgress("");
    }
  }

  const installedCount = deps.filter((d) => d.installed).length;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        检索软件可选依赖的安装状态，勾选后安装、或一键补齐在线模式所需依赖、或更新到最新版。
        安装在后端机器上执行（pip），完成后需重启后端服务生效。
      </p>

      {/* One-click + bulk actions */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => run(coreMissing, false)}
          disabled={busy || coreMissing.length === 0}
          className="text-xs bg-accent/90 hover:bg-accent text-ink font-semibold rounded px-3 py-1.5 disabled:opacity-40"
          title="安装 LLM + 在线检索所需的全部缺失依赖"
        >
          {coreMissing.length === 0
            ? "✓ 在线核心已就绪"
            : `一键安装在线核心（${coreMissing.length}）`}
        </button>
        <button
          onClick={() => run([...selected], false)}
          disabled={busy || selected.size === 0}
          className="text-xs border border-edge text-slate-300 rounded px-3 py-1.5 hover:border-accent/40 hover:text-accent disabled:opacity-40"
        >
          安装选中（{selected.size}）
        </button>
        <button
          onClick={() => run([...selected], true)}
          disabled={busy || selected.size === 0}
          className="text-xs border border-edge text-slate-300 rounded px-3 py-1.5 hover:border-accent/40 hover:text-accent disabled:opacity-40"
        >
          更新选中到最新版
        </button>
        <button
          onClick={refresh}
          disabled={busy || loading}
          className="text-xs border border-edge text-slate-400 rounded px-3 py-1.5 hover:text-slate-200 disabled:opacity-40 ml-auto"
        >
          {loading ? "刷新中…" : "刷新状态"}
        </button>
      </div>

      {loadError && (
        <div className="text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
          无法加载依赖列表：{loadError}
        </div>
      )}

      <ChemToolsCard status={chemTools} />
      <KnowledgeBaseCard stats={kbStats} onReindex={() => void reindexKb()} reindexing={kbReindexing} />

      <KbDiagnosticsCard />

      {/* Progress / result */}
      {busy && (
        <div className="text-xs rounded px-3 py-2 border border-accent/40 text-accent bg-accent/10">
          ⏳ {progress}
        </div>
      )}
      {result && !busy && (
        <div
          className={`text-xs rounded px-3 py-2 border ${
            result.ok
              ? "border-emerald-500/40 text-emerald-400 bg-emerald-500/10"
              : "border-rose-500/40 text-rose-400 bg-rose-500/10"
          }`}
        >
          <div>
            {result.ok ? "✓ " : "✗ "}
            {result.summary}
          </div>
          {(result.stdout || result.stderr) && (
            <button
              onClick={() => setShowLog((v) => !v)}
              className="mt-1 underline text-slate-400 hover:text-slate-200"
            >
              {showLog ? "隐藏日志" : "查看 pip 日志"}
            </button>
          )}
          {showLog && (
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[10px] text-slate-400 bg-ink/60 rounded p-2">
              {(result.stdout || "") + "\n" + (result.stderr || "")}
            </pre>
          )}
        </div>
      )}

      {/* Catalog grouped by extra */}
      <div className="space-y-3 max-h-[46vh] overflow-auto pr-1">
        {grouped.length === 0 && !loading && !loadError && (
          <p className="text-xs text-slate-500 py-2">正在加载依赖目录…</p>
        )}
        {grouped.map((g) => (
          <div key={g.extra}>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              {g.label}
            </div>
            <div className="space-y-1">
              {g.items.map((d) => (
                <label
                  key={d.pip_name}
                  className="flex items-start gap-2 text-xs cursor-pointer rounded px-1.5 py-1 hover:bg-white/5"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(d.pip_name)}
                    onChange={() => toggle(d.pip_name)}
                    className="mt-0.5 accent-sky-400"
                  />
                  <span
                    className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
                      d.installed ? "bg-emerald-400" : "bg-slate-600"
                    }`}
                    title={d.installed ? "已安装" : "未安装"}
                  />
                  <span className="flex-1 min-w-0">
                    <span className="font-mono text-slate-200">{d.pip_name}</span>
                    {d.installed && d.version && (
                      <span className="text-emerald-400/80 ml-1">v{d.version}</span>
                    )}
                    {!d.installed && (
                      <span className="text-slate-500 ml-1">未安装</span>
                    )}
                    <span className="block text-slate-500 leading-snug">{d.enables}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>

      <p className="text-[11px] text-slate-500 pt-1 border-t border-edge">
        已安装 {installedCount} / {deps.length} 项。安装大模型库（torch/botorch/sentence-transformers）可能耗时数分钟。
      </p>
    </div>
  );
}
