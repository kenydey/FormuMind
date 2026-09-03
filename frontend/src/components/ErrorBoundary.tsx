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
    console.error("FormuMind UI error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="h-screen flex flex-col items-center justify-center bg-ink text-slate-300 p-8">
          <h1 className="text-lg font-semibold text-slate-100 mb-2">界面发生错误</h1>
          <p className="text-sm text-slate-400 mb-4 max-w-lg text-center">
            {this.state.error.message || "未知错误"}
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
