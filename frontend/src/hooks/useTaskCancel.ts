import { useStore } from "../store";

/** Kinds that support unified cancel via store (loop/recommend/deep_research)
 *  or local AbortController + api.cancelTask (storm / kg_relations). */
export const CANCELABLE_TASK_KINDS = [
  "loop",
  "recommend",
  "deep_research",
  "wiki_storm_report",
  "kg_relations_rebuild",
] as const;

export type CancelableTaskKind = (typeof CANCELABLE_TASK_KINDS)[number];

/** 统一冷启动文案：首包 retrieve 无 message 时显示模型冷启动中 */
export function coldStartMessage(stage: string | undefined, message: string | undefined, fallback: string): string {
  if (message) return message;
  if (stage === "retrieve") return "模型冷启动中… 正在检索";
  return fallback;
}

/** 统一取消 hook：根据当前 task.kind 路由到对应的 cancel 函数，按钮样式一致.
 *  STORM / relations 由组件本地 AbortController 处理（见 HubReports / DependencyManager）。 */
export function useTaskCancel() {
  const task = useStore((s) => s.task);
  const cancelLoopTask = useStore((s) => s.cancelLoopTask);
  const cancelResearch = useStore((s) => s.cancelResearch);
  const cancelDeepResearch = useStore((s) => s.cancelDeepResearch);

  const kind = task?.kind;
  const storeCancelable =
    kind === "loop" || kind === "recommend" || kind === "deep_research";
  const canCancel = Boolean(
    task && storeCancelable && task.state !== "cancelled" && task.state !== "completed" && task.state !== "failed",
  );

  const handleCancel = () => {
    if (!task) return;
    if (kind === "loop") void cancelLoopTask();
    else if (kind === "recommend") void cancelResearch();
    else if (kind === "deep_research") void cancelDeepResearch();
    else {
      void cancelResearch();
      void cancelDeepResearch();
    }
  };

  return { task, canCancel, handleCancel, coldStartMessage };
}

export const CANCEL_BUTTON_CLASS =
  "border border-rose-500/50 text-rose-300 hover:bg-rose-500/10 rounded px-2 py-1 text-xs";
