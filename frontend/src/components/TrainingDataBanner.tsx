import { useEffect, useState } from "react";
import { useStore } from "../store";
import { useShallow } from "zustand/react/shallow";

/**
 * B: 训练数据就绪横幅 — 数据量 < min_samples 时，寻优/优化基于模型先验而非
 * 实测数据。横幅让「结果可信度」在操作前透明化，并引导用户回填实测结果。
 * 挂载时拉一次；CSV 导入成功后由 importCsv 自动刷新（充足时自动消失）。
 */
export default function TrainingDataBanner() {
  const { trainingStatus, refreshTrainingStatus } = useStore(
    useShallow((s) => ({
      trainingStatus: s.trainingStatus,
      refreshTrainingStatus: s.refreshTrainingStatus,
    }))
  );
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    void refreshTrainingStatus();
  }, [refreshTrainingStatus]);

  if (dismissed || !trainingStatus || trainingStatus.sufficient) return null;

  const total = trainingStatus.total_records;
  const min = trainingStatus.min_samples;

  return (
    <div className="shrink-0 px-5 py-2 bg-amber-500/10 border-b border-amber-500/30 flex items-center gap-3 text-xs text-amber-300">
      <span className="font-semibold shrink-0">📊 训练数据不足</span>
      <span className="text-amber-200/80 truncate">
        当前 {total} 条实测记录 &lt; 起训阈值 {min} 条 ——{" "}
        {trainingStatus.message
          ? trainingStatus.message
          : "寻优结果基于预测器先验，导入实测 CSV 后才会转为数据驱动。"}
      </span>
      <button
        onClick={() => refreshTrainingStatus()}
        className="shrink-0 text-amber-200/60 hover:text-amber-100"
        title="刷新状态"
      >
        ⟳
      </button>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-amber-200/60 hover:text-amber-100"
        title="忽略"
      >
        ✕
      </button>
    </div>
  );
}
