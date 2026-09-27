import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  formatApiError,
  type ArtifactDiffOp,
  type ArtifactDiffResponse,
  type ArtifactVersion,
  type ArtifactVersionListResponse,
} from "../api";

type Props = {
  projectId: string | null;
};

const STATUS_TONE: Record<ArtifactVersion["status"], string> = {
  staging: "text-amber-300 border-amber-400/40 bg-amber-400/10",
  pending: "text-sky-300 border-sky-400/40 bg-sky-400/10",
  finalized: "text-emerald-300 border-emerald-400/40 bg-emerald-400/10",
};

const STATUS_LABEL: Record<ArtifactVersion["status"], string> = {
  staging: "草稿",
  pending: "待审",
  finalized: "已定版",
};

const shortId = (id: string) => id.slice(0, 8);

function fmtTime(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function DiffOpView({ op, index }: { op: ArtifactDiffOp; index: number }) {
  const text = op.type === "insert" ? op.new_text : op.old_text;
  if (op.type === "equal") {
    return (
      <div
        key={index}
        data-testid="artifact-diff-op"
        data-op-type="equal"
        className="whitespace-pre-wrap text-[11px] text-slate-400"
      >
        {text}
      </div>
    );
  }
  if (op.type === "insert") {
    return (
      <div
        key={index}
        data-testid="artifact-diff-op"
        data-op-type="insert"
        className="whitespace-pre-wrap text-[11px] text-emerald-200 bg-emerald-400/10 rounded px-1"
      >
        + {text}
      </div>
    );
  }
  return (
    <div
      key={index}
      data-testid="artifact-diff-op"
      data-op-type="delete"
      className="whitespace-pre-wrap text-[11px] text-rose-200 bg-rose-400/10 rounded px-1 line-through"
    >
      − {text}
    </div>
  );
}

/**
 * W4-4: artifact version panel — lineage picker/create, version list with
 * status badges and derivation display, per-row "restore as new version"
 * (copy-on-write), and two-version diff with inline op highlighting.
 *
 * Mounted in the knowledge-hub reports pane next to ManifestDetailPanel.
 */
export default function ArtifactVersionsPanel({ projectId }: Props) {
  const [lineageId, setLineageId] = useState("");
  const [loaded, setLoaded] = useState<ArtifactVersionListResponse | null>(null);
  const [newName, setNewName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [diff, setDiff] = useState<ArtifactDiffResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string) => {
    if (!id.trim()) return;
    setBusy("load");
    setError(null);
    try {
      const resp = await api.listArtifactVersions(id.trim());
      setLoaded(resp);
      setSelected([]);
      setDiff(null);
    } catch (e) {
      setError(formatApiError(e));
      setLoaded(null);
    } finally {
      setBusy(null);
    }
  }, []);

  const createLineage = useCallback(async () => {
    if (!projectId || !newName.trim()) return;
    setBusy("create");
    setError(null);
    try {
      const lin = await api.createArtifactLineage(projectId, newName.trim());
      setLineageId(lin.lineage_id);
      await load(lin.lineage_id);
      setNewName("");
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(null);
    }
  }, [projectId, newName, load]);

  const restore = useCallback(
    async (versionId: string) => {
      setBusy(`restore:${versionId}`);
      setError(null);
      try {
        await api.restoreArtifactVersion(versionId);
        await load(lineageId);
      } catch (e) {
        setError(formatApiError(e));
      } finally {
        setBusy(null);
      }
    },
    [lineageId, load],
  );

  const toggleSelect = useCallback((versionId: string) => {
    setSelected((prev) => {
      if (prev.includes(versionId)) return prev.filter((v) => v !== versionId);
      if (prev.length >= 2) return [prev[1], versionId];
      return [...prev, versionId];
    });
  }, []);

  const runDiff = useCallback(async () => {
    if (selected.length !== 2) return;
    const [a, b] = selected;
    setBusy("diff");
    setError(null);
    try {
      const resp = await api.getArtifactVersionDiff(a, b);
      setDiff(resp);
    } catch (e) {
      setError(formatApiError(e));
      setDiff(null);
    } finally {
      setBusy(null);
    }
  }, [selected]);

  // Derivation lookup: version_id -> based_on_version_id
  const basedOn = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const e of loaded?.graph ?? []) m.set(e.version_id, e.based_on_version_id);
    return m;
  }, [loaded]);

  useEffect(() => {
    setDiff(null);
  }, [selected]);

  if (!projectId) {
    return (
      <div
        data-testid="artifact-versions-panel"
        className="rounded-lg border border-edge/70 bg-ink/40 px-3 py-2 text-[11px] text-slate-500"
      >
        版本管理：请先选择活动项目
      </div>
    );
  }

  return (
    <div
      data-testid="artifact-versions-panel"
      className="rounded-lg border border-edge/70 bg-ink/40 px-3 py-2 space-y-2"
    >
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-medium text-slate-200">版本管理</span>
        <span className="text-[10px] text-slate-500">
          staging → pending → finalized；回滚 = 基于旧版新建版本（copy-on-write）
        </span>
      </div>

      {/* lineage picker / create */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          data-testid="artifact-lineage-input"
          value={lineageId}
          onChange={(e) => setLineageId(e.target.value)}
          placeholder="lineage_id"
          className="w-44 rounded border border-edge bg-ink px-2 py-1 text-[11px] text-slate-200"
        />
        <button
          data-testid="artifact-lineage-load"
          onClick={() => void load(lineageId)}
          disabled={busy !== null || !lineageId.trim()}
          className="rounded border border-edge px-2 py-1 text-[11px] text-slate-300 disabled:opacity-40"
        >
          加载版本
        </button>
        <input
          data-testid="artifact-lineage-name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="新建逻辑文件名称"
          className="w-40 rounded border border-edge bg-ink px-2 py-1 text-[11px] text-slate-200"
        />
        <button
          data-testid="artifact-lineage-create"
          onClick={() => void createLineage()}
          disabled={busy !== null || !newName.trim()}
          className="rounded border border-edge px-2 py-1 text-[11px] text-slate-300 disabled:opacity-40"
        >
          新建
        </button>
      </div>

      {error && (
        <p data-testid="artifact-versions-error" className="text-[11px] text-rose-300">
          {error}
        </p>
      )}

      {loaded && (
        <>
          <div className="text-[11px] text-slate-400">
            {loaded.lineage.name}
            <span className="text-slate-600">（{shortId(loaded.lineage.lineage_id)}）</span>
            <span className="ml-2 text-slate-500">共 {loaded.versions.length} 个版本</span>
          </div>
          <div className="space-y-1">
            {loaded.versions.map((v) => {
              const parent = basedOn.get(v.version_id);
              return (
                <div
                  key={v.version_id}
                  data-testid={`artifact-version-row-${v.version_id}`}
                  className="flex flex-wrap items-center gap-2 rounded border border-edge/60 px-2 py-1.5"
                >
                  <input
                    type="checkbox"
                    data-testid={`artifact-diff-check-${v.version_id}`}
                    checked={selected.includes(v.version_id)}
                    onChange={() => toggleSelect(v.version_id)}
                    title="选中参与对比"
                    className="accent-sky-400"
                  />
                  <code className="text-[11px] text-slate-300">{shortId(v.version_id)}</code>
                  <span
                    className={`rounded border px-1.5 py-0.5 text-[10px] ${STATUS_TONE[v.status]}`}
                  >
                    {STATUS_LABEL[v.status]}
                  </span>
                  {parent && (
                    <span className="text-[10px] text-slate-500">
                      派生自 <code className="text-slate-400">{shortId(parent)}</code>
                    </span>
                  )}
                  <span className="text-[10px] text-slate-600">{fmtTime(v.created_at)}</span>
                  {v.actor && <span className="text-[10px] text-slate-600">{v.actor}</span>}
                  <button
                    data-testid={`artifact-restore-${v.version_id}`}
                    onClick={() => void restore(v.version_id)}
                    disabled={busy !== null}
                    className="ml-auto rounded border border-edge px-2 py-0.5 text-[11px] text-slate-300 disabled:opacity-40"
                  >
                    {busy === `restore:${v.version_id}` ? "恢复中…" : "恢复为新版"}
                  </button>
                </div>
              );
            })}
            {loaded.versions.length === 0 && (
              <p className="text-[11px] text-slate-500">该逻辑文件暂无版本。</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              data-testid="artifact-diff-run"
              onClick={() => void runDiff()}
              disabled={busy !== null || selected.length !== 2}
              className="rounded border border-edge px-2 py-1 text-[11px] text-slate-300 disabled:opacity-40"
            >
              对比选中版本（{selected.length}/2）
            </button>
            {selected.length === 2 && (
              <span className="text-[10px] text-slate-500">
                {shortId(selected[0])} ↔ {shortId(selected[1])}
              </span>
            )}
          </div>
        </>
      )}

      {diff && (
        <div data-testid="artifact-diff-view" className="space-y-1 rounded border border-edge/60 p-2">
          <div className="text-[11px] text-slate-400">
            对比 <code>{shortId(diff.version_id)}</code> ↔ <code>{shortId(diff.against_id)}</code>
            {diff.truncated && (
              <span className="ml-2 text-amber-300">大文本仅行级对比</span>
            )}
          </div>
          {diff.ops.map((op, i) => (
            <DiffOpView key={i} op={op} index={i} />
          ))}
          {diff.ops.length === 0 && (
            <p className="text-[11px] text-slate-500">两版本内容一致。</p>
          )}
        </div>
      )}
    </div>
  );
}
