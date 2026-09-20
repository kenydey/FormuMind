import { memo, useEffect, useId, useRef, useState } from "react";

type Props = {
  chart: string;
  className?: string;
};

let mermaidInit: Promise<typeof import("mermaid")> | null = null;

function loadMermaid() {
  if (!mermaidInit) {
    mermaidInit = import("mermaid").then(async (mod) => {
      const mermaid = mod.default;
      mermaid.initialize({
        startOnLoad: false,
        // Strict: no click/script; safer for untrusted wiki L2 content.
        securityLevel: "strict",
        theme: "dark",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
      });
      return mod;
    });
  }
  return mermaidInit;
}

/**
 * Lazy Mermaid renderer for Wiki Reader (S3).
 * On parse/render failure, degrades to a fenced code block (no XSS throw).
 */
function MermaidBlock({ chart, className }: Props) {
  const reactId = useId().replace(/:/g, "");
  const hostRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const source = (chart || "").replace(/\n$/, "");

  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    setErrorMsg(null);
    if (!source.trim()) {
      setFailed(true);
      setErrorMsg("empty mermaid");
      return;
    }
    (async () => {
      try {
        const mod = await loadMermaid();
        if (cancelled || !hostRef.current) return;
        const id = `fm-mermaid-${reactId}`;
        const { svg } = await mod.default.render(id, source);
        if (cancelled || !hostRef.current) return;
        // mermaid.render returns SVG string; securityLevel=strict omits scripts.
        hostRef.current.innerHTML = svg;
      } catch (e) {
        if (!cancelled) {
          setFailed(true);
          setErrorMsg(e instanceof Error ? e.message : "mermaid render failed");
          if (hostRef.current) hostRef.current.innerHTML = "";
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, reactId]);

  if (failed) {
    return (
      <pre
        className="my-1.5 rounded bg-ink/60 p-2 overflow-x-auto text-[12px]"
        data-testid="wiki-mermaid-fallback"
        title={errorMsg || "mermaid failed"}
      >
        <code className="language-mermaid">{source}</code>
      </pre>
    );
  }

  return (
    <div
      className={`my-2 overflow-x-auto rounded border border-edge/40 bg-ink/30 p-2 ${className || ""}`}
      data-testid="wiki-mermaid-block"
    >
      <div ref={hostRef} className="min-h-[2rem] [&_svg]:max-w-full" />
    </div>
  );
}

export default memo(MermaidBlock);
