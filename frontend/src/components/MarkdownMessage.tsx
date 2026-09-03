import { memo, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

/**
 * Chemistry-aware Markdown renderer for assistant chat messages.
 *
 * - GFM tables (formulation tables survive intact)
 * - LaTeX math via KaTeX ($$…$$ reaction equations)
 * - ```smiles fenced blocks render as 2D structure drawings (smiles-drawer),
 *   falling back to the raw string when the SMILES fails to parse.
 */

function SmilesDrawing({ smiles }: { smiles: string }) {
  const ref = useRef<SVGSVGElement>(null);
  const failed = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const el = ref.current;
    if (!el) return;
    (async () => {
      try {
        const mod = await import("smiles-drawer");
        const SmilesDrawer = (mod as { default?: unknown }).default ?? mod;
        const SD = SmilesDrawer as {
          SvgDrawer: new (opts: Record<string, unknown>) => {
            draw: (tree: unknown, target: SVGSVGElement, theme: string) => void;
          };
          parse: (s: string, ok: (tree: unknown) => void, err: () => void) => void;
        };
        const drawer = new SD.SvgDrawer({ width: 260, height: 180, bondThickness: 1 });
        SD.parse(
          smiles.trim(),
          (tree) => {
            if (!cancelled && ref.current) drawer.draw(tree, ref.current, "dark");
          },
          () => {
            failed.current = true;
          }
        );
      } catch {
        failed.current = true;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [smiles]);

  return (
    <span className="inline-block my-1 rounded border border-edge/60 bg-ink/40 p-1">
      <svg ref={ref} width={260} height={180} />
      <code className="block text-[10px] text-slate-500 px-1 pb-0.5 break-all">{smiles.trim()}</code>
    </span>
  );
}

function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="md-message leading-relaxed [&_table]:my-2 [&_table]:w-full [&_table]:text-[12px] [&_th]:border [&_th]:border-edge/60 [&_th]:px-1.5 [&_th]:py-0.5 [&_th]:bg-ink/50 [&_td]:border [&_td]:border-edge/60 [&_td]:px-1.5 [&_td]:py-0.5 [&_p]:my-1.5 [&_ul]:my-1.5 [&_ul]:pl-4 [&_ul]:list-disc [&_ol]:my-1.5 [&_ol]:pl-4 [&_ol]:list-decimal [&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-2 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-2 [&_h3]:text-sm [&_h3]:font-medium [&_h3]:mt-1.5 [&_blockquote]:border-l-2 [&_blockquote]:border-edge [&_blockquote]:pl-2 [&_blockquote]:text-slate-400 [&_a]:text-accent [&_hr]:border-edge/60 [&_pre]:my-1.5 [&_pre]:rounded [&_pre]:bg-ink/60 [&_pre]:p-2 [&_pre]:overflow-x-auto [&_.katex-display]:my-2 [&_.katex-display]:overflow-x-auto">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          code({ className, children, ...props }) {
            const text = String(children ?? "");
            if (/language-smiles/.test(className || "")) {
              return <SmilesDrawing smiles={text} />;
            }
            return (
              <code className={`${className || ""} text-[12px]`} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownMessage);
