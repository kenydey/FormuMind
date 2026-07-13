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
        ChemCrow 工具网关 · {okCount}/{caps.length} 可用
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
        <span
          className={`px-1.5 py-0.5 rounded border ${
            stats.embedding_available
              ? "border-teal-500/40 bg-teal-500/10 text-teal-300"
              : "border-edge bg-ink/60 text-slate-500"
          }`}
          title={
            stats.embedding_available
              ? "sentence-transformers 已安装，检索为语义向量模式"
              : "未安装 sentence-transformers，检索为关键词模式（可在下方一键安装 Embedding）"
          }
        >
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
  science: "科学计算 · Science",
  optimize: "寻优器 · Optimize",
  bo: "高斯过程寻优 · BoTorch",
  pydoe: "经典 DOE · pyDOE",
  baybe: "贝叶斯主动学习 · BayBE",
  color: "色差 · Color",
  file_ingest: "文件解析 · Ingest",
  export: "导出 · Export",
  notebooklm: "NotebookLM",
};
const EXTRA_ORDER = Object.keys(EXTRA_LABELS);

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
