import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface the exact file:line of the crashing render — the message alone
    // ("reading 'slice' of undefined") is not enough to locate the source.
    const stack = (error.stack || "").split("\n").slice(0, 12).join("\n");
    const frame = (info.componentStack || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .slice(0, 8)
      .join("\n");
    console.error("FormuMind UI error:", error, info.componentStack);
    console.error("FormuMind UI error stack:\n" + stack + "\ncomponentStack:\n" + frame);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="h-screen flex flex-col items-center justify-center bg-ink text-slate-300 p-8">
          <h1 className="text-lg font-semibold text-slate-100 mb-2">界面发生错误</h1>
          <p className="text-sm text-slate-400 mb-4 max-w-lg text-center font-mono break-all">
            {this.state.error.message || "未知错误"}
          </p>
          <p className="text-xs text-slate-500 mb-4 font-mono text-center whitespace-pre-wrap max-w-2xl max-h-40 overflow-auto">
            {(() => {
              const frame = (this.state.error.stack || "")
                .split("\n")
                .filter((l) => l.includes("FormuMind") || l.includes("src/") || l.includes("<"))
                .slice(0, 4)
                .join("\n");
              return frame || "";
            })()}
          </p>
          <button
            type="button"
            className="text-xs border border-edge hover:border-accent/40 rounded px-3 py-1.5 text-accent"
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
