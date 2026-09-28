import { useEffect, useState } from "react";
import { formatApiError } from "../../api";
import {
  collectionsApi,
  type CollectionDetail,
  type CollectionSummary,
} from "../../api/domains/collections";
import { useStore } from "../../store";

function fmtTime(ts?: number | null): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { hour12: false });
}

/** W6-3 / P2-3 Smart Collections — list / create / detail with snapshot diffs. */
export default function HubCollectionsPane({ active }: { active: boolean }) {
  const projectId = useStore((s) => s.activeProjectId);
  const [list, setList] = useState<CollectionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CollectionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // create form
  const [fName, setFName] = useState("");
  const [fQuery, setFQuery] = useState("");
  const [fDateFrom, setFDateFrom] = useState("");
  const [fDateTo, setFDateTo] = useState("");
  const [fDomains, setFDomains] = useState("");
  const [fPreset, setFPreset] = useState("");
  const [fSchedOn, setFSchedOn] = useState(true);
  const [fInterval, setFInterval] = useState("24");

  const load = async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const r = await collectionsApi.list(projectId);
      setList(r.collections);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (active) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, projectId]);

  const openDetail = async (id: string) => {
    if (!projectId) return;
    setSelectedId(id);
    setDetail(null);
    try {
      setDetail(await collectionsApi.detail(projectId, id));
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const doCreate = async () => {
    if (!projectId || !fName.trim() || !fQuery.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const filters: Record<string, unknown> = {};
      if (fDateFrom.trim()) filters.date_from = fDateFrom.trim();
      if (fDateTo.trim()) filters.date_to = fDateTo.trim();
      const domains = fDomains.split(",").map((s) => s.trim()).filter(Boolean);
      if (domains.length) filters.domain_allowlist = domains;
      const col = await collectionsApi.create(projectId, {
        name: fName.trim(),
        query: fQuery.trim(),
        filters,
        screening_preset: fPreset.trim() || undefined,
        schedule: {
          enabled: fSchedOn,
          interval_hours: Math.max(1, Number(fInterval) || 24),
        },
      });
      setList((prev) => [col, ...prev]);
      setFName("");
      setFQuery("");
      setFDateFrom("");
      setFDateTo("");
      setFDomains("");
      setFPreset("");
      void openDetail(col.collection_id);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setCreating(false);
    }
  };

  const doRefresh = async (id: string) => {
    if (!projectId) return;
    setRefreshing(true);
    setError(null);
    try {
      await collectionsApi.refresh(projectId, id);
      await load();
      if (selectedId === id) await openDetail(id);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setRefreshing(false);
    }
  };

  const doDelete = async (id: string) => {
    if (!projectId) return;
    try {
      await collectionsApi.remove(projectId, id);
      setList((prev) => prev.filter((c) => c.collection_id !== id));
      if (selectedId === id) {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  return (
    <div className="h-full overflow-y-auto space-y-3 pr-1" data-testid="hub-collections-pane">
      <div className="rounded-lg border border-edge/60 bg-ink/30 p-3">
        <h3 className="text-sm text-slate-100">Smart Collections</h3>
        <p className="text-[10px] text-slate-500 mt-0.5">
          保存的查询 + 筛选规则：手动或按计划自动刷新，结果进文献 manifest（screening_source=collection），每次刷新留 snapshot diff
        </p>
      </div>

      {error && (
        <div className="text-xs text-rose-300 rounded border border-rose-500/30 bg-rose-950/30 p-2" data-testid="hub-collections-error">
          {error}
        </div>
      )}

      {/* create form */}
      <div className="rounded-lg border border-edge/60 bg-ink/30 p-3 space-y-2">
        <div className="text-xs text-slate-300">新建集合</div>
        <input
          data-testid="hub-collections-name"
          className="w-full rounded bg-ink/60 border border-edge/60 px-2 py-1 text-xs text-slate-200"
          placeholder="名称，如 VIANT 防腐文献"
          value={fName}
          onChange={(e) => setFName(e.target.value)}
        />
        <input
          data-testid="hub-collections-query"
          className="w-full rounded bg-ink/60 border border-edge/60 px-2 py-1 text-xs text-slate-200"
          placeholder="查询，如 waterborne conversion coating corrosion"
          value={fQuery}
          onChange={(e) => setFQuery(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-2">
          <input
            data-testid="hub-collections-date-from"
            className="rounded bg-ink/60 border border-edge/60 px-2 py-1 text-xs text-slate-200"
            placeholder="起始年份，如 2020"
            value={fDateFrom}
            onChange={(e) => setFDateFrom(e.target.value)}
          />
          <input
            data-testid="hub-collections-date-to"
            className="rounded bg-ink/60 border border-edge/60 px-2 py-1 text-xs text-slate-200"
            placeholder="截止年份，如 2026"
            value={fDateTo}
            onChange={(e) => setFDateTo(e.target.value)}
          />
        </div>
        <input
          data-testid="hub-collections-domains"
          className="w-full rounded bg-ink/60 border border-edge/60 px-2 py-1 text-xs text-slate-200"
          placeholder="域名白名单（逗号分隔），如 sciencedirect.com"
          value={fDomains}
          onChange={(e) => setFDomains(e.target.value)}
        />
        <div className="grid grid-cols-2 gap-2">
          <input
            data-testid="hub-collections-preset"
            className="rounded bg-ink/60 border border-edge/60 px-2 py-1 text-xs text-slate-200"
            placeholder="screening 预设（可选）"
            value={fPreset}
            onChange={(e) => setFPreset(e.target.value)}
          />
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                data-testid="hub-collections-sched-on"
                checked={fSchedOn}
                onChange={(e) => setFSchedOn(e.target.checked)}
              />
              定时刷新
            </label>
            <input
              data-testid="hub-collections-interval"
              className="w-16 rounded bg-ink/60 border border-edge/60 px-1 py-1 text-xs text-slate-200"
              value={fInterval}
              onChange={(e) => setFInterval(e.target.value)}
              title="小时"
            />
            <span className="text-[10px] text-slate-500">小时</span>
          </div>
        </div>
        <button
          data-testid="hub-collections-create"
          className="rounded bg-emerald-600/80 hover:bg-emerald-500/80 px-3 py-1 text-xs text-white disabled:opacity-40"
          disabled={creating || !fName.trim() || !fQuery.trim()}
          onClick={doCreate}
        >
          {creating ? "创建中…" : "创建集合"}
        </button>
      </div>

      {/* list */}
      <div className="space-y-2">
        {loading && <div className="text-xs text-slate-500">加载中…</div>}
        {!loading && list.length === 0 && (
          <div className="text-xs text-slate-500" data-testid="hub-collections-empty">
            暂无集合
          </div>
        )}
        {list.map((c) => (
          <div
            key={c.collection_id}
            className="rounded-lg border border-edge/60 bg-ink/30 p-3"
            data-testid={`hub-collection-${c.collection_id}`}
          >
            <div className="flex items-start justify-between gap-2">
              <button
                className="text-left text-sm text-slate-100 hover:text-emerald-300"
                onClick={() => void openDetail(c.collection_id)}
                data-testid={`hub-collection-open-${c.collection_id}`}
              >
                {c.name}
              </button>
              <div className="flex items-center gap-1">
                <button
                  className="rounded border border-edge/60 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-ink/60 disabled:opacity-40"
                  disabled={refreshing}
                  onClick={() => void doRefresh(c.collection_id)}
                  data-testid={`hub-collection-refresh-${c.collection_id}`}
                >
                  {refreshing ? "刷新中…" : "刷新"}
                </button>
                <button
                  className="rounded border border-edge/60 px-2 py-0.5 text-[11px] text-rose-300 hover:bg-rose-950/40"
                  onClick={() => void doDelete(c.collection_id)}
                  data-testid={`hub-collection-delete-${c.collection_id}`}
                >
                  删除
                </button>
              </div>
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5 truncate">{c.query}</div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1 text-[10px] text-slate-500">
              <span>{c.schedule.enabled ? `⏱ 每 ${c.schedule.interval_hours}h` : "⏸ 定时关闭"}</span>
              <span>上次运行：{fmtTime(c.schedule.last_run)}</span>
              <span>snapshots：{c.snapshot_count}</span>
              {c.last_snapshot && (
                <span className="text-slate-400">
                  上次：共 {c.last_snapshot.total} · +{c.last_snapshot.added} · −{c.last_snapshot.removed}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* detail: snapshot timeline */}
      {selectedId && (
        <div className="rounded-lg border border-edge/60 bg-ink/30 p-3 space-y-2" data-testid="hub-collection-detail">
          <div className="flex items-center justify-between">
            <div className="text-xs text-slate-300">
              {detail ? `集合详情：${detail.name}` : "加载详情…"}
            </div>
            <button
              className="text-[11px] text-slate-500 hover:text-slate-300"
              onClick={() => {
                setSelectedId(null);
                setDetail(null);
              }}
            >
              关闭
            </button>
          </div>
          {detail && (
            <div className="space-y-2">
              {detail.snapshots.length === 0 && (
                <div className="text-[11px] text-slate-500">暂无 snapshot，点击"刷新"生成第一次快照</div>
              )}
              {[...detail.snapshots].reverse().map((s) => (
                <div
                  key={s.snapshot_id}
                  className="rounded border border-edge/40 bg-ink/50 p-2"
                  data-testid={`hub-snapshot-${s.snapshot_id}`}
                >
                  <div className="text-[11px] text-slate-400">
                    {fmtTime(s.at)} · 共 {s.total} ·{" "}
                    <span className="text-emerald-300">+{s.added.length}</span> ·{" "}
                    <span className="text-rose-300">−{s.removed.length}</span>
                    <span className="text-slate-600">（{s.actor}）</span>
                  </div>
                  {s.added.length > 0 && (
                    <div className="mt-1 text-[11px]">
                      <span className="text-emerald-300">新增：</span>
                      <span className="text-slate-300">{s.added.slice(0, 10).join(", ")}</span>
                      {s.added.length > 10 && <span className="text-slate-500"> …等 {s.added.length} 篇</span>}
                    </div>
                  )}
                  {s.removed.length > 0 && (
                    <div className="mt-1 text-[11px]">
                      <span className="text-rose-300">移除：</span>
                      <span className="text-slate-300">{s.removed.slice(0, 10).join(", ")}</span>
                      {s.removed.length > 10 && <span className="text-slate-500"> …等 {s.removed.length} 篇</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
