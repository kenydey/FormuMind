import { useStore } from "../store";

// Minimal markdown-ish renderer: headings, bold, list items, code spans.
function renderLine(line: string, i: number) {
  if (line.startsWith("### ")) return <h3 key={i} className="text-accent2 font-semibold mt-3 mb-1">{line.slice(4)}</h3>;
  if (line.startsWith("**") && line.endsWith("**")) return <p key={i} className="text-slate-200 font-semibold mt-2">{line.slice(2, -2)}</p>;
  const html = line
    .replace(/`([^`]+)`/g, '<code class="text-accent font-mono text-xs">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong class="text-slate-100">$1</strong>')
    .replace(/_([^_]+)_/g, '<em class="text-slate-400">$1</em>');
  return <p key={i} className="text-sm leading-relaxed text-slate-300" dangerouslySetInnerHTML={{ __html: html }} />;
}

export default function ChatPanel() {
  const { research, busy, error, task } = useStore();

  return (
    <section className="glass rounded-xl p-4 overflow-y-auto flex flex-col">
      <h2 className="text-sm uppercase tracking-widest text-accent2 mb-2">研究与推理 · AI Research Stream</h2>

      {error && <div className="text-red-400 text-sm mb-2">⚠ {error}</div>}

      {busy === "researching" && <div className="text-accent text-sm animate-pulse">检索专利 / 文献，构建 RAG 知识库…</div>}

      {busy === "optimizing" && task && (
        <div className="text-accent2 text-sm mb-3">
          贝叶斯寻优闭环：{task.message} ({Math.round(task.progress * 100)}%)
          <div className="h-1.5 bg-edge rounded mt-1 overflow-hidden">
            <div className="h-full bg-accent2 transition-all" style={{ width: `${task.progress * 100}%` }} />
          </div>
        </div>
      )}

      {!research && busy === "idle" && (
        <p className="text-slate-500 text-sm mt-4">
          在左侧设定研发需求，点击 <span className="text-accent">①</span> 启动专利检索与配方推荐。
        </p>
      )}

      {research && (
        <div className="flex flex-col gap-0.5">
          {research.chat_markdown.split("\n").map((l, i) => renderLine(l, i))}
        </div>
      )}
    </section>
  );
}
