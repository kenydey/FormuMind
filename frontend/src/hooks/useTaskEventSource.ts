import { useEffect, useState } from "react";
import { subscribeTaskStream, type TaskProgressEvent } from "../api";

/** React hook wrapping EventSource task progress stream with cleanup on unmount. */
export function useTaskEventSource(taskId: string | null) {
  const [event, setEvent] = useState<TaskProgressEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!taskId) return;
    setEvent(null);
    setError(null);
    setDone(false);

    const es = subscribeTaskStream(
      taskId,
      (ev) => {
        setEvent(ev);
        if (ev.status === "COMPLETED" || ev.status === "FAILED") {
          setDone(true);
          if (ev.status === "FAILED") {
            setError(ev.message || "任务失败");
          }
        }
      },
      () => {
        setError("SSE 连接中断");
      }
    );

    return () => es.close();
  }, [taskId]);

  return { event, error, done };
}
