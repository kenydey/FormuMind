import { useStore } from "../store";

/** 统一冷启动文案：首包 retrieve 无 message 时显示模型冷启动中 */
export function coldStartMessage(stage: string | undefined, message: string | undefined, fallback: string): string {
  if (message) return message;
  if (stage === "retrieve") return "模型冷启动中… 正在检索";
  return fallback;
}

/** 统一取消 hook：根据当前 task.kind 路由到对应的 cancel 函数，按钮样式一致 */
export function useTaskCancel() {
  const task = useStore((s) => s.task);
  const cancelLoopTask = useStore((s) => s.cancelLoopTask);
  const cancelResearch = useStore((s) => s.cancelResearch);
  const cancelDeepResearch = useStore((s) => s.cancelDeepResearch);

  const kind = task?.kind;
  const canCancel = Boolean(task && (kind === "loop" || kind === "recommend" || kind === "deep_research") && task.state !== "cancelled" && task.state !== "completed" && task.state !== "failed");

  const handleCancel = () => {
    if (!task) return;
    if (kind === "loop") void cancelLoopTask();
    else if (kind === "recommend") void cancelResearch();
    else if (kind === "deep_research") void cancelDeepResearch();
    else {
      // 回退：直接调 cancelResearch/deepResearch 的 abort 已无 taskId 时走 api 层已处理
      void cancelResearch();
      void cancelDeepResearch();
    }
  };

  return { task, canCancel, handleCancel, coldStartMessage };
}

export const CANCEL_BUTTON_CLASS = "border border-rose-500/50 text-rose-300 hover:bg-rose-500/10 rounded px-2 py-1 text-xs";
