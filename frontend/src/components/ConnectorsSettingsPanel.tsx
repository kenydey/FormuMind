import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  formatApiError,
  type BuiltinConnector,
  type McpImportPreview,
  type McpImportResponse,
  type McpServerConfig,
} from "../api";

type AddMode = null | "github" | "upload" | "paste" | "manual";

export default function ConnectorsSettingsPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [builtin, setBuiltin] = useState<BuiltinConnector[]>([]);
  const [mcp, setMcp] = useState<McpServerConfig[]>([]);
  const [mcpEnabled, setMcpEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [addMode, setAddMode] = useState<AddMode>(null);
  const [githubUrl, setGithubUrl] = useState("");
  const [pasteJson, setPasteJson] = useState("");
  const [preview, setPreview] = useState<McpImportPreview | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [draftId, setDraftId] = useState("local-mcp");
  const [draftCmd, setDraftCmd] = useState("");
  const [draftArgs, setDraftArgs] = useState("");
  const [probeMsg, setProbeMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    setError(null);
    return api
      .listConnectors()
      .then((res) => {
        setBuiltin(res.builtin);
        setMcp(res.mcp);
        setMcpEnabled(res.mcp_client_enabled);
      })
      .catch((e) => setError(formatApiError(e)));
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  function resetImportUi() {
    setAddMode(null);
    setPreview(null);
    setImportId(null);
    setGithubUrl("");
    setPasteJson("");
  }

  function applyImportResponse(res: McpImportResponse) {
    if (res.preview) setPreview(res.preview);
    if (res.dry_run && res.import_id) setImportId(res.import_id);
    if (res.imported && res.mcp) {
      setMcp(res.mcp);
      resetImportUi();
    }
  }

  async function toggleBuiltin(id: string, enabled: boolean) {
    try {
      const res = await api.toggleBuiltinConnector(id, enabled);
      setBuiltin(res.builtin);
    } catch (e) {
      setError(formatApiError(e));
    }
  }

  async function previewGithub() {
    if (!githubUrl.trim()) return;
    setBusy(true);
    setError(null);
    try {
      applyImportResponse(await api.importMcpGithub({ url: githubUrl.trim(), dry_run: true }));
    } catch (e) {
      setError(formatApiError(e));
      setPreview(null);
      setImportId(null);
    } finally {
      setBusy(false);
    }
  }

  async function previewPaste() {
    if (!pasteJson.trim()) return;
    setBusy(true);
    setError(null);
    try {
      applyImportResponse(await api.importMcpJson({ json_text: pasteJson, dry_run: true }));
    } catch (e) {
      setError(formatApiError(e));
      setPreview(null);
      setImportId(null);
    } finally {
      setBusy(false);
    }
  }

  async function previewUpload(file: File) {
    setBusy(true);
    setError(null);
    try {
      applyImportResponse(await api.importMcpUpload(file, true));
    } catch (e) {
      setError(formatApiError(e));
      setPreview(null);
      setImportId(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmImport() {
    if (!importId) return;
    setBusy(true);
    setError(null);
    try {
      applyImportResponse(await api.confirmMcpImport(importId));
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveMcpManual() {
    if (!draftCmd.trim()) return;
    setBusy(true);
    setError(null);
    try {
      // Import-shaped single server via JSON path so it works even when client flag is off.
      const config = {
        mcpServers: {
          [draftId.trim() || "mcp"]: {
            command: draftCmd.trim(),
            args: draftArgs
              .split(/\s+/)
              .map((x) => x.trim())
              .filter(Boolean),
          },
        },
      };
      const dry = await api.importMcpJson({ config, dry_run: true });
      if (!dry.import_id) throw new Error(dry.detail || "预览失败");
      const done = await api.confirmMcpImport(dry.import_id);
      if (done.mcp) setMcp(done.mcp);
      setAddMode(null);
      setProbeMsg(null);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleServer(id: string, enabled: boolean) {
    try {
      const res = await api.setMcpServerEnabled(id, enabled);
      setMcp(res.mcp);
    } catch (e) {
      setError(formatApiError(e));
    }
  }

  async function removeServer(id: string) {
    try {
      const res = await api.deleteMcpServer(id);
      setMcp(res.mcp);
    } catch (e) {
      setError(formatApiError(e));
    }
  }

  async function probe(id: string) {
    setProbeMsg(null);
    try {
      const res = await api.probeMcpServer(id);
      setProbeMsg(
        res.ok
          ? `✓ 已连接 · tools: ${(res.tools || []).join(", ") || "(none)"}`
          : `✗ ${res.error || "probe failed"}`,
      );
    } catch (e) {
      setProbeMsg(formatApiError(e));
    }
  }

  return (
    <div className="space-y-4" data-testid="connectors-settings-panel">
      <p className="text-xs text-slate-500 leading-relaxed">
        内置 <strong className="text-slate-300">Literature / Chemistry</strong> 只读连接器。自定义 MCP 支持从
        GitHub / 上传 / 粘贴 JSON（Claude Desktop · Cursor 形状）导入：先预览再一键确认；导入后
        <strong className="text-slate-300">默认禁用</strong>，需手动启用。运行时 Probe 仍需开启{" "}
        <code className="text-slate-400">mcp_client_enabled</code>。
      </p>
      {error && (
        <p className="text-xs text-rose-300 border border-rose-500/30 rounded px-2 py-1">{error}</p>
      )}

      <div>
        <h3 className="text-xs uppercase tracking-widest text-accent2 mb-2">内置 Connectors</h3>
        <ul className="space-y-2">
          {builtin.map((c) => (
            <li
              key={c.id}
              className="flex items-start gap-3 rounded border border-edge/60 bg-ink/40 px-2.5 py-2"
            >
              <div className="flex-1 min-w-0">
                <div className="text-sm text-slate-200">{c.display_name}</div>
                <div className="text-[11px] text-slate-500 mt-0.5">{c.description}</div>
                <div className="text-[10px] text-slate-600 mt-1">{c.use_when}</div>
              </div>
              <label className="flex items-center gap-1.5 text-[11px] text-slate-400 shrink-0">
                <input
                  type="checkbox"
                  checked={c.enabled}
                  onChange={(e) => void toggleBuiltin(c.id, e.target.checked)}
                />
                启用
              </label>
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-2">
        <h3 className="text-xs uppercase tracking-widest text-accent2">MCP（实验）</h3>
        {!mcpEnabled && (
          <p className="text-[11px] text-amber-200/80 border border-amber-500/30 rounded px-2 py-1.5">
            MCP Client 运行时默认关闭（可先导入配置）。Probe / 工具调用需在「环境变量」开启
            mcp_client_enabled。
          </p>
        )}

        <div className="flex flex-wrap gap-2" data-testid="mcp-add-menu">
          <span className="text-[11px] text-slate-500 self-center">+ 添加 MCP</span>
          <button
            type="button"
            className="text-[11px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/40"
            data-testid="mcp-add-github"
            onClick={() => {
              setAddMode("github");
              setPreview(null);
              setImportId(null);
            }}
          >
            从 GitHub
          </button>
          <button
            type="button"
            className="text-[11px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/40"
            data-testid="mcp-add-upload"
            onClick={() => {
              setAddMode("upload");
              fileRef.current?.click();
            }}
          >
            上传 JSON
          </button>
          <button
            type="button"
            className="text-[11px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/40"
            data-testid="mcp-add-paste"
            onClick={() => {
              setAddMode("paste");
              setPreview(null);
              setImportId(null);
            }}
          >
            粘贴 JSON
          </button>
          <button
            type="button"
            className="text-[11px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/40"
            onClick={() => setAddMode("manual")}
          >
            手动填写
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            data-testid="mcp-upload-input"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void previewUpload(f);
              e.target.value = "";
            }}
          />
        </div>

        {addMode === "github" && (
          <div className="rounded border border-edge/60 bg-ink/40 p-3 space-y-2" data-testid="mcp-github-form">
            <input
              className="w-full bg-panel border border-edge rounded px-2 py-1.5 text-xs text-slate-200"
              value={githubUrl}
              onChange={(e) => setGithubUrl(e.target.value)}
              placeholder="owner/repo 或 URL（自动找 mcp.json / .cursor/mcp.json）"
              data-testid="mcp-github-url"
            />
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy || !githubUrl.trim()}
                onClick={() => void previewGithub()}
                className="text-[11px] px-2.5 py-1 rounded border border-accent/50 text-accent disabled:opacity-40"
              >
                预览
              </button>
              <button type="button" onClick={resetImportUi} className="text-[11px] text-slate-500">
                取消
              </button>
            </div>
          </div>
        )}

        {addMode === "paste" && (
          <div className="rounded border border-edge/60 bg-ink/40 p-3 space-y-2" data-testid="mcp-paste-form">
            <textarea
              className="w-full h-36 bg-panel border border-edge rounded px-2 py-1.5 text-xs font-mono text-slate-200"
              value={pasteJson}
              onChange={(e) => setPasteJson(e.target.value)}
              placeholder={'{\n  "mcpServers": {\n    "name": { "command": "npx", "args": [] }\n  }\n}'}
              data-testid="mcp-paste-json"
            />
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy || !pasteJson.trim()}
                onClick={() => void previewPaste()}
                className="text-[11px] px-2.5 py-1 rounded border border-accent/50 text-accent disabled:opacity-40"
              >
                预览
              </button>
              <button type="button" onClick={resetImportUi} className="text-[11px] text-slate-500">
                取消
              </button>
            </div>
          </div>
        )}

        {addMode === "manual" && (
          <div className="rounded border border-edge/60 bg-ink/40 p-3 space-y-2">
            <div className="grid gap-2 sm:grid-cols-3">
              <input
                className="bg-ink border border-edge rounded px-2 py-1 text-xs"
                placeholder="id"
                value={draftId}
                onChange={(e) => setDraftId(e.target.value)}
              />
              <input
                className="bg-ink border border-edge rounded px-2 py-1 text-xs sm:col-span-2"
                placeholder="command (e.g. npx)"
                value={draftCmd}
                onChange={(e) => setDraftCmd(e.target.value)}
              />
              <input
                className="bg-ink border border-edge rounded px-2 py-1 text-xs sm:col-span-3"
                placeholder="args (space-separated)"
                value={draftArgs}
                onChange={(e) => setDraftArgs(e.target.value)}
              />
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy || !draftCmd.trim()}
                onClick={() => void saveMcpManual()}
                className="text-[11px] px-2.5 py-1 rounded border border-accent/50 text-accent disabled:opacity-40"
              >
                保存（默认禁用）
              </button>
              <button type="button" onClick={resetImportUi} className="text-[11px] text-slate-500">
                取消
              </button>
            </div>
          </div>
        )}

        {preview && (
          <div
            className="rounded border border-accent/30 bg-accent/5 p-3 space-y-2"
            data-testid="mcp-import-preview"
          >
            <div className="text-[11px] text-slate-400">
              来源 {preview.source}
              {preview.source_url ? ` · ${preview.source_url}` : ""}
            </div>
            <ul className="space-y-1.5">
              {preview.servers.map((s) => (
                <li key={s.id} className="text-[11px] text-slate-300 font-mono">
                  <span className="text-accent">{s.id}</span>: {s.command} {(s.args || []).join(" ")}
                  {s.env_keys?.length ? ` · env[${s.env_keys.join(",")}]` : ""}
                </li>
              ))}
            </ul>
            {(preview.warnings || []).length > 0 && (
              <ul className="text-[11px] text-amber-300/90 list-disc pl-4">
                {preview.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy || !importId}
                onClick={() => void confirmImport()}
                className="text-[11px] px-3 py-1.5 rounded bg-accent/20 border border-accent text-accent disabled:opacity-40"
                data-testid="mcp-import-confirm"
              >
                一键导入
              </button>
              <button type="button" onClick={resetImportUi} className="text-[11px] text-slate-500">
                取消
              </button>
            </div>
          </div>
        )}

        <ul className="space-y-2">
          {mcp.length === 0 ? (
            <li className="text-[11px] text-slate-600">暂无已保存的 MCP 服务器</li>
          ) : (
            mcp.map((s) => (
              <li
                key={s.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded border border-edge/60 px-2.5 py-2 text-[11px]"
                data-testid={`mcp-row-${s.id}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="text-slate-200 font-mono truncate">
                    {s.id}: {s.command} {(s.args || []).join(" ")}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <label className="flex items-center gap-1 text-slate-400">
                    <input
                      type="checkbox"
                      checked={Boolean(s.enabled)}
                      onChange={(e) => void toggleServer(s.id, e.target.checked)}
                    />
                    启用
                  </label>
                  <button
                    type="button"
                    className="text-accent border border-edge rounded px-2 py-0.5 disabled:opacity-40"
                    disabled={!mcpEnabled}
                    onClick={() => void probe(s.id)}
                    title={mcpEnabled ? "Probe tools/list" : "需开启 mcp_client_enabled"}
                  >
                    Probe
                  </button>
                  <button
                    type="button"
                    className="text-rose-300/80"
                    onClick={() => void removeServer(s.id)}
                  >
                    删除
                  </button>
                </div>
              </li>
            ))
          )}
        </ul>
        {probeMsg && <p className="text-[11px] text-slate-400">{probeMsg}</p>}
      </div>
    </div>
  );
}
