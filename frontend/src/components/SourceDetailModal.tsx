/**
 * 资料详情(B1/B2, 2026-09-05): 查看该文档在 KB 中的全部切块(页码/段落,
 * 回答引用可追溯) + 一键「链入知识图谱」提取实体建立关系。
 */
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import Modal from "./Modal";
import TableBadges from "./TableBadges";
import ExtractionTablesPanel from "./ExtractionTablesPanel";
import "./CitationRenderer.css"; // W3-14: 复用 citation-flash 高亮动画
import { api, type KbChunk } from "../api";

export default function SourceDetailModal({
  title,
  sourceId,
  onClose,
  focusPage = null,
}: {
  title: string;
  sourceId: string;
  onClose: () => void;
  /** W3-14: 打开后自动滚动到该页码的首个切块(页码 badge 跳转)。 */
  focusPage?: number | null;
}) {
  const [chunks, setChunks] = useState<KbChunk[] | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);
  const [linkReport, setLinkReport] = useState<string | null>(null);
  const [flashIdx, setFlashIdx] = useState<number | null>(null);
  /** Up-5A: 切块 / 结构化表格公式 页签 */
  const [detailTab, setDetailTab] = useState<"chunks" | "structured">("chunks");
  /** P-6: 切块客户端分页（后端 by-source 接口无分页参数）——600+ chunk 时防全量 DOM。 */
  const CHUNK_PAGE_SIZE = 50;
  const [page, setPage] = useState(0);
  /**
   * P-6 渲染层：零依赖虚拟化。分页已将挂载节点上限锁在 50，
   * 每张卡再加 content-visibility:auto，浏览器跳过视口外卡片的
   * 布局/绘制（滚动容器内生效），contain-intrinsic-size 给出预估
   * 高度避免滚动条跳动。focusPage 的 scrollIntoView 不受影响
   * （浏览器在滚动到时自动渲染被跳过的子树）。
   */
  const CHUNK_CARD_STYLE: CSSProperties = {
    contentVisibility: "auto",
    containIntrinsicSize: "0 220px",
  };

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    setPage(0);
    try {
      // B1 接线缺口修复：后端 by-source 默认 limit=200，>200 chunk 的文档会被截断。
      // 循环拉取全量（后端上限 2000/页），再走客户端分页。
      const all: KbChunk[] = [];
      const PAGE = 2000;
      for (;;) {
        const batch = await api.kbChunksBySource(sourceId, PAGE, all.length);
        all.push(...batch);
        if (batch.length < PAGE) break;
      }
      setChunks(all);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [sourceId]);

  useEffect(() => {
    if (sourceId) void load();
  }, [sourceId, load]);

  const totalPages = chunks
    ? Math.max(1, Math.ceil(chunks.length / CHUNK_PAGE_SIZE))
    : 1;
  const pageStart = page * CHUNK_PAGE_SIZE;
  const pageChunks = chunks ? chunks.slice(pageStart, pageStart + CHUNK_PAGE_SIZE) : [];

  // W3-14: 切块加载完成后, 跳转到 focusPage 的首个切块并高亮。
  useEffect(() => {
    if (busy || !chunks || focusPage == null) return;
    const idx = chunks.findIndex((c) => c.page === focusPage);
    if (idx < 0) return;
    setFlashIdx(idx);
    // P-6: 目标 chunk 可能在别的分页上 —— 先翻到对应页再滚动定位。
    setPage(Math.floor(idx / CHUNK_PAGE_SIZE));
    const t = window.setTimeout(() => {
      const el = document.getElementById(`source-chunk-${focusPage}-${idx}`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("citation-flash");
        window.setTimeout(() => el.classList.remove("citation-flash"), 2000);
      }
    }, 100);
    return () => window.clearTimeout(t);
  }, [busy, chunks, focusPage]);

  async function linkToKg() {
    setLinking(true);
    setLinkReport(null);
    setError(null);
    try {
      const r = await api.kgLinkSource(sourceId);
      setLinkReport(`图谱已更新: ${r.entities_upserted} 实体 / ${r.relations_upserted} 关系 / ${r.links_created} 链接`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLinking(false);
    }
  }

  return (
    <Modal title={`📄 资料切块 · ${title.slice(0, 40)}`} open onClose={onClose} size="lg" testId="modal-source-detail">
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className="text-[10px] border border-edge rounded-full px-2 py-1 text-slate-400 hover:text-accent"
          >
            刷新
          </button>
          <button
            type="button"
            disabled={linking}
            onClick={() => void linkToKg()}
            className="text-[10px] border border-accent2/50 rounded-full px-2 py-1 text-accent2 hover:bg-accent2/10 disabled:opacity-50"
            title="从全文提取实体并建立到知识图谱(实体/关系由 NLP 链路产出)"
          >
            {linking ? "链入中…" : "🕸 链入知识图谱"}
          </button>
          {linkReport && <span className="text-[11px] text-emerald-400">{linkReport}</span>}
        </div>
        {error && <div className="text-xs text-rose-400 bg-rose-500/10 rounded p-2">{error}</div>}
        {/* W3-7: 表格抽取 badge（kind 标签 + 行列预览） */}
        <TableBadges sourceId={sourceId} />
        {/* Up-5A: 切块 / 结构化表格公式 页签 */}
        <div className="flex gap-1 border-b border-edge">
          {(
            [
              ["chunks", "切块"],
              ["structured", "表格与公式"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              data-testid={`source-detail-tab-${id}`}
              onClick={() => setDetailTab(id)}
              className={`text-xs px-3 py-1.5 -mb-px border-b-2 transition-colors ${
                detailTab === id
                  ? "border-accent text-accent"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {detailTab === "structured" ? (
          <ExtractionTablesPanel sourceId={sourceId} />
        ) : busy ? (
          <div className="text-xs text-slate-500 py-8 text-center">切块加载中…</div>
        ) : chunks && chunks.length > 0 ? (
          <div>
            {totalPages > 1 && (
              <div
                className="flex items-center justify-between text-[11px] text-slate-500 mb-2"
                data-testid="source-chunk-pager"
              >
                <span>
                  共 {chunks.length} 块 · 第 {page + 1} / {totalPages} 页
                </span>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page <= 0}
                    className="border border-edge rounded px-2 py-0.5 disabled:opacity-40 hover:text-slate-200"
                    data-testid="source-chunk-prev"
                  >
                    上一页
                  </button>
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={page >= totalPages - 1}
                    className="border border-edge rounded px-2 py-0.5 disabled:opacity-40 hover:text-slate-200"
                    data-testid="source-chunk-next"
                  >
                    下一页
                  </button>
                </div>
              </div>
            )}
            <div className="space-y-2 max-h-[55vh] overflow-auto pr-1">
              {pageChunks.map((c, i) => {
                // 全局序号：分页不改变 testid/锚点编号，旧测试与页码跳转不受影响
                const gi = pageStart + i;
                const text = (c.text ?? c.content ?? "").trim();
                if (!text) return null;
                const loc =
                  c.page != null
                    ? `p.${c.page}${c.paragraph != null ? ` · ¶${c.paragraph}` : ""}`
                    : c.paragraph != null
                      ? `¶${c.paragraph}`
                      : (c.offset_start ?? c.offset) != null
                        ? `+${c.offset_start ?? c.offset}`
                        : "";
                return (
                  <div
                    key={c.id ?? c.chunk_id ?? gi}
                    id={`source-chunk-${c.page ?? "na"}-${gi}`}
                    data-testid={`source-chunk-${gi}`}
                    style={CHUNK_CARD_STYLE}
                    className={`border rounded p-2 bg-ink/30 transition-colors ${
                      flashIdx === gi ? "border-accent/60" : "border-edge"
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[9px] font-mono text-slate-600">#{gi + 1}</span>
                      {loc && <span className="text-[9px] font-mono text-slate-500">{loc}</span>}
                    </div>
                    <p className="text-[11px] text-slate-300 whitespace-pre-wrap leading-relaxed">
                      {text.slice(0, 600)}
                      {text.length > 600 && <span className="text-slate-600"> …</span>}
                    </p>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className="text-xs text-slate-500 py-8 text-center">
            该文档暂无切块(可能未入库或处于示例语料)。
          </div>
        )}
      </div>
    </Modal>
  );
}
