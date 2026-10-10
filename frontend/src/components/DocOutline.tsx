import { useMemo, useState } from "react";
import type { KbChunk } from "../api";

/**
 * PageIndex 借鉴 B2: 文档大纲侧边栏（纯前端视图）。
 *
 * 数据来自已加载的 KbChunk[]（heading_path + page），零后端改动。
 * 点击节点 → onSelectNode(page) 由父组件复用 focusPage 滚动机制定位到该节首个 chunk。
 */
interface OutlineNode {
  title: string;
  page: number | null;
  children: OutlineNode[];
}

function buildTree(chunks: KbChunk[]): OutlineNode[] {
  const roots: OutlineNode[] = [];
  const findOrCreate = (
    siblings: OutlineNode[],
    title: string,
    page: number | null,
  ): OutlineNode => {
    let n = siblings.find((s) => s.title === title);
    if (!n) {
      n = { title, page, children: [] };
      siblings.push(n);
    } else if (n.page == null && page != null) {
      n.page = page;
    }
    return n;
  };
  for (const c of chunks) {
    const hp = (c.heading_path || "").trim();
    if (!hp) continue;
    const parts = hp.split(" > ").map((s) => s.trim()).filter(Boolean);
    if (!parts.length) continue;
    let siblings = roots;
    for (const part of parts) {
      const node = findOrCreate(siblings, part, c.page ?? null);
      if (node.page == null && c.page != null) node.page = c.page;
      siblings = node.children;
    }
  }
  return roots;
}

function NodeView({
  node,
  depth,
  onSelect,
}: {
  node: OutlineNode;
  depth: number;
  onSelect: (page: number | null) => void;
}) {
  const [open, setOpen] = useState(depth === 0);
  return (
    <div>
      <div
        className="flex items-center gap-1 py-0.5 pr-1 rounded hover:bg-accent/10 cursor-pointer"
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
        onClick={() => onSelect(node.page)}
        data-testid="doc-outline-node"
      >
        {node.children.length > 0 ? (
          <button
            type="button"
            className="shrink-0 text-slate-500 hover:text-accent text-[10px] w-3"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(!open);
            }}
          >
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="shrink-0 w-3" />
        )}
        <span className="truncate text-[11px] text-slate-300" title={node.title}>
          {node.title}
        </span>
        {node.page != null && (
          <span className="shrink-0 text-[9px] font-mono text-accent/70 ml-auto">
            p.{node.page}
          </span>
        )}
      </div>
      {open &&
        node.children.map((ch, i) => (
          <NodeView key={`${ch.title}-${i}`} node={ch} depth={depth + 1} onSelect={onSelect} />
        ))}
    </div>
  );
}

export default function DocOutline({
  chunks,
  onSelectNode,
}: {
  chunks: KbChunk[];
  onSelectNode: (page: number | null) => void;
}) {
  const [visible, setVisible] = useState(false);
  const tree = useMemo(() => buildTree(chunks), [chunks]);
  if (!tree.length) return null;
  return (
    <div className="border border-edge rounded-lg">
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        className="w-full flex items-center gap-2 px-2 py-1.5 text-[11px] text-slate-400 hover:text-accent"
        data-testid="doc-outline-toggle"
      >
        <span>{visible ? "▾" : "▸"}</span>
        <span>📑 文档大纲（{tree.length} 节）</span>
      </button>
      {visible && (
        <div className="max-h-64 overflow-y-auto px-1 pb-2" data-testid="doc-outline">
          {tree.map((n, i) => (
            <NodeView key={`${n.title}-${i}`} node={n} depth={0} onSelect={onSelectNode} />
          ))}
        </div>
      )}
    </div>
  );
}
