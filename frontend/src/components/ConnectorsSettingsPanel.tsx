import { useCallback, useEffect, useState } from "react";
import {
  api,
  formatApiError,
  type BuiltinConnector,
  type McpServerConfig,
} from "../api";

export default function ConnectorsSettingsPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [builtin, setBuiltin] = useState<BuiltinConnector[]>([]);
  const [mcp, setMcp] = useState<McpServerConfig[]>([]);
  const [mcpEnabled, setMcpEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftId, setDraftId] = useState("local-mcp");
  const [draftCmd, setDraftCmd] = useState("");
  const [draftArgs, setDraftArgs] = useState("");
  const [probeMsg, setProbeMsg] = useState<string | null>(null);

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

  async function toggleBuiltin(id: string, enabled: boolean) {
    try {
      const res = await api.toggleBuiltinConnector(id, enabled);
      setBuiltin(res.builtin);
    } catch (e) {
      setError(formatApiError(e));
    }
  }

  async function saveMcp() {
    if (!draftCmd.trim()) return;
    try {
      const servers: McpServerConfig[] = [
        ...mcp.filter((s) => s.id !== draftId.trim()),
        {
          id: draftId.trim() || "mcp",
          command: draftCmd.trim(),
          args: draftArgs
            .split(/\s+/)
            .map((x) => x.trim())
            .filter(Boolean),
          enabled: true,
          transport: "stdio",
        },
      ];
      const res = await api.replaceMcpServers(servers);
      setMcp(res.mcp);
      setProbeMsg(null);
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
        内置 <strong className="text-slate-300">Literature / Chemistry</strong> 只读连接器；自定义 MCP
        需在环境变量开启 <code className="text-slate-400">mcp_client_enabled</code>。
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

      <div>
        <h3 className="text-xs uppercase tracking-widest text-accent2 mb-2">MCP（实验）</h3>
        {!mcpEnabled ? (
          <p className="text-[11px] text-amber-200/80 border border-amber-500/30 rounded px-2 py-1.5">
            MCP Client 默认关闭。请在「环境变量」中开启 mcp_client_enabled。
          </p>
        ) : (
          <div className="space-y-2">
            {mcp.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between gap-2 rounded border border-edge/60 px-2 py-1.5 text-[11px]"
              >
                <span className="text-slate-300 font-mono truncate">
                  {s.id}: {s.command} {(s.args || []).join(" ")}
                </span>
                <button
                  type="button"
                  className="text-accent border border-edge rounded px-2 py-0.5"
                  onClick={() => void probe(s.id)}
                >
                  Probe
                </button>
              </div>
            ))}
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
            <button
              type="button"
              onClick={() => void saveMcp()}
              className="text-xs bg-accent/90 text-ink font-semibold rounded px-3 py-1"
            >
              保存 MCP 服务器
            </button>
            {probeMsg && <p className="text-[11px] text-slate-400">{probeMsg}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
