import { useEffect, useRef, useState } from "react";
import { api, formatApiError, type QCMeasurementView, type QCReportResult } from "../api";

interface QCReportModalProps {
  campaignId: number;
  rowId: number;
  onClose: () => void;
  onUploaded?: () => void;
}

/**
 * Upload a test certificate bound to one specific workbench row.
 *
 * Launched from the row's context menu, so the campaign + row are already
 * known — no cross-campaign dropdown. The point of the binding is that a
 * measurement stops being a loose number: it arrives with the standard it was
 * run under and the acceptance window it was judged against, traceable to the
 * document it came from. One row can hold many reports (salt-spray, adhesion,
 * VOC, ...), so the table lists the row's accumulated measurements.
 */

function Verdict({ m }: { m: QCMeasurementView }) {
  if (m.passed === null) {
    return <span className="text-slate-500" title="报告未给出验收指标">未判定</span>;
  }
  return m.passed ? (
    <span className="text-green-400">合格</span>
  ) : (
    <span className="text-red-400">超差</span>
  );
}

function specRange(m: QCMeasurementView): string {
  if (m.spec_min != null && m.spec_max != null) return `${m.spec_min} ~ ${m.spec_max}`;
  if (m.spec_min != null) return `≥ ${m.spec_min}`;
  if (m.spec_max != null) return `≤ ${m.spec_max}`;
  return "—";
}

export default function QCReportModal({
  campaignId,
  rowId,
  onClose,
  onUploaded,
}: QCReportModalProps) {
  const [result, setResult] = useState<QCReportResult | null>(null);
  const [existing, setExisting] = useState<QCMeasurementView[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .getWorkbenchRowMeasurements(campaignId, rowId)
      .then((r) => setExisting(r.measurements))
      .catch(() => setExisting([]));
  }, [campaignId, rowId, result]);

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(
        await api.uploadQcReport(file, { campaign_id: campaignId, row_id: rowId })
      );
      onUploaded?.();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  const shown = result?.measurements.length ? result.measurements : existing;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-edge rounded-lg shadow-xl w-[42rem] max-w-[94vw] p-4 text-sm max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-slate-200">上传检测报告（行 #{rowId}）</h3>
          <button className="text-slate-400 hover:text-slate-200" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        <p className="text-slate-400 text-xs mb-3">
          上传检测报告（PDF / Word / Markdown / 图片），自动提取带
          <span className="text-accent">单位、检测方法、规格限</span>
          的计量项并绑定到本行。同一份报告重复上传不会重复计入。
        </p>

        <div className="flex items-center gap-2 mb-3">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.doc,.md,.txt,.csv,.png,.jpg,.jpeg"
            className="flex-1 text-xs text-slate-400 file:mr-2 file:px-3 file:py-1.5
                       file:rounded file:border file:border-edge file:bg-panel file:text-slate-300"
          />
          <button
            className="px-3 py-1.5 rounded bg-accent/20 border border-accent/40 text-accent
                       hover:bg-accent/30 disabled:opacity-50"
            onClick={upload}
            disabled={busy}
          >
            {busy ? "解析中…" : "📄 上传并解析"}
          </button>
        </div>

        {error && (
          <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2 mb-2 text-xs">
            {error}
          </div>
        )}

        {result && (
          <div className="text-xs space-y-1 mb-2">
            <div className="text-slate-400">
              解析器 {result.parser} · 提取 {result.measurement_count} 项
              {result.already_attached && " · 该报告此前已绑定，未重复计入"}
            </div>
            {result.message && (
              <div className="text-yellow-400/80 bg-yellow-400/10 border border-yellow-400/20 rounded p-2">
                {result.message}
              </div>
            )}
            {Object.keys(result.synced_measured).length > 0 && (
              <div className="text-green-400">
                ✓ 已同步进可训练数据：{Object.keys(result.synced_measured).join("、")}
              </div>
            )}
          </div>
        )}

        {shown.length > 0 && (
          <div className="overflow-x-auto">
            <div className="text-slate-300 font-medium mb-1">
              本行计量项（共 {shown.length} 条）
            </div>
            <table className="w-full text-xs">
              <thead className="text-slate-400">
                <tr>
                  <th className="text-left py-1">检测项</th>
                  <th className="text-right">实测值</th>
                  <th className="text-left pl-2">单位</th>
                  <th className="text-left">检测方法</th>
                  <th className="text-left">技术要求</th>
                  <th className="text-left">判定</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((m, i) => (
                  <tr key={`${m.metric}-${i}`} className="border-t border-edge/50">
                    <td className="py-1 text-slate-200">{m.metric}</td>
                    <td className="text-right">{m.value}</td>
                    <td className="pl-2 text-slate-400">{m.unit || "—"}</td>
                    <td className={m.test_method ? "text-slate-300" : "text-yellow-400/70"}>
                      {m.test_method || "未注明"}
                    </td>
                    <td className="text-slate-400">{specRange(m)}</td>
                    <td>
                      <Verdict m={m} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
