import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError, type MemoryItem } from "../../api";

type ScopeFilter = "" | "global" | "project" | "user";

const SCOPE_LABEL: Record<Exclude<ScopeFilter, "">, string> = {
  global: "全局",
  project: "项目",
  user: "个人",
};

const PAGE_SIZE = 20;

/** W3-8: agent memory management — list / scope filter / search / delete. */
export default function MemoryPanel({ reloadKey }: { reloadKey?: number }) {
  const [scope, setScope] = useState<ScopeFilter>("");
  const [q, setQ] = useState("");
  const [qDraft, setQDraft] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  /** F-4: 序号守卫 —— 筛选/翻页快速切换时旧请求不覆盖新数据。 */
  const loadSeq = useRef(0);
  /** F-4: onDelete 闭包里的 load 可能绑定旧 scope/q/page；loadRef 始终指向最新 load，
   *  避免删除完成后的刷新把旧筛选的数据写回新视图。 */
  const loadRef = useRef<() => Promise<void>>(async () => {});

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.listMemories({
        scope: scope || undefined,
        q: q || undefined,
        page,
        page_size: PAGE_SIZE,
      });
      if (loadSeq.current !== seq) return;
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      if (loadSeq.current !== seq) return;
      setItems([]);
      setTotal(0);
      setError(formatApiError(e));
    } finally {
      if (loadSeq.current === seq) setLoading(false);
    }
  }, [scope, q, page]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  // F-4: 每轮渲染同步最新 load，供 onDelete 等动作回调使用（幂等赋值）。
  loadRef.current = load;

  function onScopeChange(next: ScopeFilter) {
    setScope(next);
    setPage(1);
  }

  function onSearch() {
    setQ(qDraft.trim());
    setPage(1);
  }

  async function onDelete(item: MemoryItem) {
    if (!window.confirm(`删除这条记忆？\n${item.key}: ${item.value}`)) return;
    setDeleting(item.id);
    try {
      await api.deleteMemory(item.id);
      // F-4: 删除耗时中筛选/翻页可能已变化 —— 用最新 load 刷新当前视图，
      // 旧闭包的 load 不再直接调用，避免旧筛选数据覆盖新视图。
      await loadRef.current();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setDeleting(null);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-3" data-testid="memory-panel">
      <p className="text-[11px] text-slate-500 leading-relaxed">
        Agent 长期记忆：跨会话保留的偏好与事实，按全局 / 项目 / 个人三域隔离。
        写入时会自动拦截密钥与提示词注入内容。
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded border border-edge overflow-hidden text-xs">
          {([["", "全部"], ["global", "全局"], ["project", "项目"], ["user", "个人"]] as const).map(
            ([v, label]) => (
              <button
                key={v}
                onClick={() => onScopeChange(v)}
                className={`px-3 py-1.5 transition-colors ${
                  scope === v
                    ? "bg-accent/20 text-accent"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {label}
              </button>
            )
          )}
        </div>
        <div className="flex gap-1.5 flex-1 min-w-[200px]">
          <input
            value={qDraft}
            onChange={(e) => setQDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSearch()}
            placeholder="搜索 key / value…"
            aria-label="搜索记忆"
            className="flex-1 bg-ink border border-edge rounded px-2 py-1.5 text-xs"
          />
          <button
            onClick={onSearch}
            className="text-xs border border-edge text-slate-300 rounded px-3 hover:border-accent/40 hover:text-accent"
          >
            搜索
          </button>
        </div>
      </div>

      {error && (
        <div className="text-xs rounded px-3 py-2 border border-rose-500/40 text-rose-400 bg-rose-500/10">
          加载失败：{error}
        </div>
      )}

      {loading ? (
        <p className="text-xs text-slate-500 py-4 text-center">加载中…</p>
      ) : items.length === 0 ? (
        <p className="text-xs text-slate-500 py-4 text-center" data-testid="memory-empty">
          暂无记忆{scope ? `（${SCOPE_LABEL[scope]}）` : ""}。
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              data-testid={`memory-item-${item.id}`}
              className="rounded border border-edge/60 px-3 py-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm text-slate-200 break-all">{item.key}</span>
                    <span className="text-[10px] px-1 py-px rounded border border-edge text-slate-500">
                      {SCOPE_LABEL[item.scope]}
                      {item.scope !== "global" && item.scope_id ? ` · ${item.scope_id}` : ""}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5 break-all">{item.value}</p>
                  <p className="text-[10px] text-slate-600 mt-1">
                    更新于 {new Date(item.updated_at).toLocaleString()}
                  </p>
                </div>
                <button
                  onClick={() => void onDelete(item)}
                  disabled={deleting === item.id}
                  aria-label={`删除记忆 ${item.key}`}
                  className="shrink-0 text-xs border border-edge text-slate-500 rounded px-2 py-1 hover:text-rose-400 hover:border-rose-500/40 disabled:opacity-40"
                >
                  {deleting === item.id ? "删除中…" : "删除"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>
            共 {total} 条 · 第 {page} / {totalPages} 页
          </span>
          <div className="flex gap-1.5">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="border border-edge rounded px-2 py-1 disabled:opacity-40 hover:text-slate-200"
            >
              上一页
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="border border-edge rounded px-2 py-1 disabled:opacity-40 hover:text-slate-200"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
