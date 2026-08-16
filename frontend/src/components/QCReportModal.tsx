import { useEffect, useRef, useState } from "react";
import {
  api,
  formatApiError,
  type QCMeasurementView,
  type QCReportResult,
  type WorkbenchCampaignSummary,
  type WorkbenchRow,
} from "../api";

/**
 * Upload a test certificate and bind its results to a workbench row.
 *
 * The point of the binding is that a measurement stops being a loose number:
 * it arrives with the standard it was run under and the acceptance window it
 * was judged against, and it can be traced back to the document it came from.
 * Binding targets a workbench row (campaign + row) — the same entity the lab
 * bench shows — not the internal training-experiment table.
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

export default function QCReportModal() {
  const [campaigns, setCampaigns] = useState<WorkbenchCampaignSummary[]>([]);
  const [campaignId, setCampaignId] = useState<number | null>(null);
  const [rows, setRows] = useState<WorkbenchRow[]>([]);
  const [rowId, setRowId] = useState<number | null>(null);
  const [result, setResult] = useState<QCReportResult | null>(null);
  const [existing, setExisting] = useState<QCMeasurementView[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .listWorkbenchCampaigns()
      .then((list) => {
        setCampaigns(list);
        if (list.length && campaignId === null) setCampaignId(list[0].id);
      })
      .catch((err) => setError(formatApiError(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (campaignId === null) return;
    api
      .getWorkbenchCampaign(campaignId)
      .then((r) => {
        setRows(r.rows);
        setRowId((prev) =>
          prev != null && r.rows.some((x) => x.id === prev)
            ? prev
            : (r.rows[0]?.id ?? null)
        );
      })
      .catch(() => setRows([]));
  }, [campaignId]);

  // After a successful upload, show the experiment's accumulated measurements.
  useEffect(() => {
    if (!result?.experiment_id) return;
    api
      .experimentMeasurements(result.experiment_id)
      .then((r) => setExisting(r.measurements))
      .catch(() => setExisting([]));
  }, [result]);

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file || campaignId === null || rowId === null) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(
        await api.uploadQcReport(file, { campaign_id: campaignId, row_id: rowId })
      );
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  }

  const shown = result?.measurements.length ? result.measurements : existing;

  return (
    <div className="space-y-4 text-sm">
      <p className="text-slate-400">
        上传检测报告（PDF / Word / Markdown / 图片），自动提取带
        <span className="text-accent">单位、检测方法、规格限</span>
        的计量项并绑定到台账实验行。同一份报告重复上传不会重复计入。
      </p>

      {campaigns.length === 0 ? (
        <div className="text-slate-500">
          暂无实验台账 Campaign——先在「实验台账」生成 DOE 计划。
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-400">Campaign</span>
            <select
              className="bg-panel border border-edge rounded px-2 py-1 flex-1 min-w-[14rem]"
              value={campaignId ?? ""}
              onChange={(e) => {
                setCampaignId(Number(e.target.value));
                setRowId(null);
              }}
            >
              {campaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  #{c.id} · {c.name} · {c.row_count} 行 · {c.status}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-400">绑定实验行</span>
            <select
              className="bg-panel border border-edge rounded px-2 py-1 flex-1 min-w-[14rem]"
              value={rowId ?? ""}
              onChange={(e) => setRowId(Number(e.target.value))}
              disabled={rows.length === 0}
            >
              {rows.map((r) => (
                <option key={r.id} value={r.id}>
                  行 #{r.id} · {r.status}
                  {Object.keys(r.measurements || {}).length
                    ? ` · ${Object.keys(r.measurements).length} 项实测`
                    : ""}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.doc,.md,.txt,.csv,.png,.jpg,.jpeg"
              className="flex-1 text-xs text-slate-400 file:mr-2 file:px-3 file:py-1.5
                         file:rounded file:border file:border-edge file:bg-panel
                         file:text-slate-300"
            />
            <button
              className="px-3 py-1.5 rounded bg-accent/20 border border-accent/40 text-accent
                         hover:bg-accent/30 disabled:opacity-50"
              onClick={upload}
              disabled={busy || campaignId === null || rowId === null}
            >
              {busy ? "解析中…" : "📄 上传并解析"}
            </button>
          </div>
        </>
      )}

      {error && (
        <div className="text-red-400 bg-red-400/10 border border-red-400/20 rounded p-2">
          {error}
        </div>
      )}

      {result && (
        <div className="text-xs space-y-1">
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
            该实验的计量项（共 {shown.length} 条）
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
  );
}
