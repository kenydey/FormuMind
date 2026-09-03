import type { ReactNode } from "react";
import { CHART_THEME } from "./chartUtils";

interface ChartContainerProps {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export default function ChartContainer({ title, children, actions, className = "" }: ChartContainerProps) {
  return (
    <div className={`rounded-lg border border-edge bg-ink/60 p-3 ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-medium text-slate-300">{title}</h3>
        {actions && <div className="flex items-center gap-1">{actions}</div>}
      </div>
      <div className="relative w-full" style={{ minHeight: 200 }}>
        {children}
      </div>
    </div>
  );
}
