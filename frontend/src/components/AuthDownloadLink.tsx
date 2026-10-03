import { useState, type ReactNode } from "react";
import { formatApiError } from "../api";
import { downloadWithAuth } from "../utils/download";

type Props = {
  url: string;
  /** Used when the response carries no Content-Disposition filename. */
  filename?: string;
  className?: string;
  title?: string;
  testId?: string;
  children: ReactNode;
};

/**
 * Link-looking control that downloads an API file *with* the bearer token.
 * ``href`` stays on the anchor (copy-link, no-JS fallback) but a click is
 * intercepted: see {@link downloadWithAuth} for why a plain link breaks under auth.
 */
export default function AuthDownloadLink({ url, filename, className, title, testId, children }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onClick = async (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await downloadWithAuth(url, filename);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <a
        href={url}
        className={className}
        title={title}
        data-testid={testId}
        aria-busy={busy}
        onClick={(e) => void onClick(e)}
      >
        {busy ? "下载中…" : children}
      </a>
      {error && (
        <span role="alert" className="text-[10px] text-rose-400 ml-1" data-testid={testId ? `${testId}-error` : undefined}>
          {error}
        </span>
      )}
    </>
  );
}
