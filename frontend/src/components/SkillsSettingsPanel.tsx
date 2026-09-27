import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  formatApiError,
  type SkillInstallPreview,
  type SkillInstallResponse,
  type UnifiedSkill,
} from "../api";

type AddMode = null | "github" | "upload" | "paste";

export default function SkillsSettingsPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [skills, setSkills] = useState<UnifiedSkill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [addMode, setAddMode] = useState<AddMode>(null);
  const [githubUrl, setGithubUrl] = useState("");
  const [pasteMd, setPasteMd] = useState("");
  const [preview, setPreview] = useState<SkillInstallPreview | null>(null);
  const [installId, setInstallId] = useState<string | null>(null);
  const [previewWarnings, setPreviewWarnings] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    setError(null);
    return api
      .listSkills()
      .then((res) => setSkills(res.skills))
      .catch((e) => setError(formatApiError(e)));
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  function resetInstallUi() {
    setAddMode(null);
    setPreview(null);
    setInstallId(null);
    setPreviewWarnings([]);
    setGithubUrl("");
    setPasteMd("");
  }

  function applyInstallResponse(res: SkillInstallResponse) {
    if (res.preview) setPreview(res.preview);
    setPreviewWarnings(res.preview?.warnings || []);
    if (res.dry_run && res.install_id) {
      setInstallId(res.install_id);
    }
    if (res.installed && res.catalog) {
      setSkills(res.catalog.skills);
      resetInstallUi();
    }
  }

  async function toggle(skill: UnifiedSkill, enabled: boolean) {
    setBusy(true);
    try {
      const disabled = skills
        .filter((s) => (s.id === skill.id ? !enabled : !s.enabled))
        .filter((s) => s.activation_policy !== "always-on")
        .map((s) => s.id);
      const pinned = skills.filter((s) => s.pinned).map((s) => s.id);
      const res = await api.patchSkillsPrefs({ disabled_ids: disabled, pinned_ids: pinned });
      setSkills(res.skills);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function previewGithub() {
    if (!githubUrl.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.installSkillGithub({ url: githubUrl.trim(), dry_run: true });
      applyInstallResponse(res);
    } catch (e) {
      setError(formatApiError(e));
      setPreview(null);
      setInstallId(null);
    } finally {
      setBusy(false);
    }
  }

  async function previewPaste() {
    if (!pasteMd.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.installSkillPaste({ markdown: pasteMd, dry_run: true });
      applyInstallResponse(res);
    } catch (e) {
      setError(formatApiError(e));
      setPreview(null);
      setInstallId(null);
    } finally {
      setBusy(false);
    }
  }

  async function previewUpload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.installSkillUpload(file, true);
      applyInstallResponse(res);
    } catch (e) {
      setError(formatApiError(e));
      setPreview(null);
      setInstallId(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmInstall() {
    if (!installId) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.confirmSkillInstall(installId);
      applyInstallResponse(res);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function uninstall(skill: UnifiedSkill) {
    if (skill.origin === "bundled") return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.uninstallSkill(skill.id);
      if (res.catalog) setSkills(res.catalog.skills);
      else await load();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  }

  const playbooks = skills.filter((s) => s.kind === "playbook");
  const chatSkills = skills.filter((s) => s.kind === "chat_skill");

  return (
    <div className="space-y-4" data-testid="skills-settings-panel">
      <p className="text-xs text-slate-500 leading-relaxed">
        统一管理<strong className="text-slate-300">配方行动包</strong>（推荐/DOE/寻优等，默认「配方推荐」按当前产品域自适应）与
        <strong className="text-slate-300">对话技能</strong>（SKILL.md）。设置启停，中栏「+」启动。支持从 GitHub /
        本地上传 / 粘贴<strong className="text-slate-300">一键安装</strong>（先预览审查，再确认写入）。
      </p>

      <div className="flex flex-wrap gap-2" data-testid="skills-add-menu">
        <span className="text-[11px] text-slate-500 self-center">+ 添加技能</span>
        <button
          type="button"
          className="text-[11px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/40"
          onClick={() => {
            setAddMode("github");
            setPreview(null);
            setInstallId(null);
          }}
          data-testid="skills-add-github"
        >
          从 GitHub
        </button>
        <button
          type="button"
          className="text-[11px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/40"
          onClick={() => {
            setAddMode("upload");
            setPreview(null);
            setInstallId(null);
            fileRef.current?.click();
          }}
          data-testid="skills-add-upload"
        >
          上传本地包
        </button>
        <button
          type="button"
          className="text-[11px] px-2 py-1 rounded border border-edge text-slate-300 hover:border-accent/40"
          onClick={() => {
            setAddMode("paste");
            setPreview(null);
            setInstallId(null);
          }}
          data-testid="skills-add-paste"
        >
          粘贴 SKILL.md
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".zip,.md,.markdown,application/zip,text/markdown"
          className="hidden"
          data-testid="skills-upload-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void previewUpload(f);
            e.target.value = "";
          }}
        />
      </div>

      {addMode === "github" && (
        <div className="rounded border border-edge/60 bg-ink/40 p-3 space-y-2" data-testid="skills-github-form">
          <label className="block text-[11px] text-slate-400">
            GitHub URL 或 owner/repo[/path][@ref]
            <input
              className="mt-1 w-full bg-panel border border-edge rounded px-2 py-1.5 text-xs text-slate-200"
              value={githubUrl}
              onChange={(e) => setGithubUrl(e.target.value)}
              placeholder="https://github.com/org/repo/tree/main/my-skill"
              data-testid="skills-github-url"
            />
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || !githubUrl.trim()}
              onClick={() => void previewGithub()}
              className="text-[11px] px-2.5 py-1 rounded border border-accent/50 text-accent disabled:opacity-40"
            >
              预览
            </button>
            <button type="button" onClick={resetInstallUi} className="text-[11px] text-slate-500">
              取消
            </button>
          </div>
        </div>
      )}

      {addMode === "paste" && (
        <div className="rounded border border-edge/60 bg-ink/40 p-3 space-y-2" data-testid="skills-paste-form">
          <textarea
            className="w-full h-36 bg-panel border border-edge rounded px-2 py-1.5 text-xs font-mono text-slate-200"
            value={pasteMd}
            onChange={(e) => setPasteMd(e.target.value)}
            placeholder={"---\nname: my-skill\ndescription: ...\nallowed_tools: kb_hybrid\n---\n\n# ..."}
            data-testid="skills-paste-md"
          />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || !pasteMd.trim()}
              onClick={() => void previewPaste()}
              className="text-[11px] px-2.5 py-1 rounded border border-accent/50 text-accent disabled:opacity-40"
            >
              预览
            </button>
            <button type="button" onClick={resetInstallUi} className="text-[11px] text-slate-500">
              取消
            </button>
          </div>
        </div>
      )}

      {preview && (
        <div
          className="rounded border border-accent/30 bg-accent/5 p-3 space-y-2"
          data-testid="skills-install-preview"
        >
          <div className="text-sm text-slate-200 font-semibold">{preview.name}</div>
          <p className="text-[11px] text-slate-400">{preview.summary || preview.description}</p>
          <div className="text-[10px] text-slate-500">
            origin={preview.origin || "—"}
            {preview.pinned_sha ? ` · sha=${preview.pinned_sha.slice(0, 8)}` : ""}
            {preview.allowed_tools?.length
              ? ` · tools: ${preview.allowed_tools.join(", ")}`
              : ""}
          </div>
          {previewWarnings.length > 0 && (
            <ul className="text-[11px] text-amber-300/90 list-disc pl-4">
              {previewWarnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              disabled={busy || !installId}
              onClick={() => void confirmInstall()}
              className="text-[11px] px-3 py-1.5 rounded bg-accent/20 border border-accent text-accent disabled:opacity-40"
              data-testid="skills-install-confirm"
            >
              一键安装
            </button>
            <button type="button" onClick={resetInstallUi} className="text-[11px] text-slate-500">
              取消
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="text-xs text-rose-300 border border-rose-500/30 rounded px-2 py-1">{error}</p>
      )}
      <Section
        title="配方行动包 · Playbooks"
        rows={playbooks}
        busy={busy}
        onToggle={toggle}
        onUninstall={uninstall}
      />
      <Section
        title="对话技能 · Chat Skills"
        rows={chatSkills}
        busy={busy}
        onToggle={toggle}
        onUninstall={uninstall}
      />
    </div>
  );
}

function originBadge(origin?: string) {
  const o = origin || "bundled";
  const tone =
    o === "github"
      ? "border-sky-500/40 text-sky-300"
      : o === "local"
        ? "border-emerald-500/40 text-emerald-300"
        : "border-edge text-slate-500";
  return (
    <span className={`text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded border ${tone}`}>
      {o}
    </span>
  );
}

function Section({
  title,
  rows,
  busy,
  onToggle,
  onUninstall,
}: {
  title: string;
  rows: UnifiedSkill[];
  busy: boolean;
  onToggle: (s: UnifiedSkill, enabled: boolean) => void;
  onUninstall: (s: UnifiedSkill) => void;
}) {
  return (
    <div>
      <h3 className="text-xs uppercase tracking-widest text-accent2 mb-2">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-[11px] text-slate-600">暂无</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((s) => (
            <li
              key={s.id}
              className="flex items-start gap-3 rounded border border-edge/60 bg-ink/40 px-2.5 py-2"
              data-testid={`skill-row-${s.id}`}
            >
              <span className="text-base shrink-0">{s.icon || "✦"}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="text-sm text-slate-200">{s.title}</div>
                  {originBadge(s.origin)}
                </div>
                <div className="text-[11px] text-slate-500 mt-0.5 line-clamp-2">{s.summary}</div>
                <div className="text-[10px] text-slate-600 mt-1">
                  {s.kind} · {s.activation_policy} · {s.id}
                </div>
              </div>
              <div className="flex flex-col items-end gap-1.5 shrink-0">
                <label className="flex items-center gap-1.5 text-[11px] text-slate-400">
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    disabled={busy || s.activation_policy === "always-on"}
                    onChange={(e) => onToggle(s, e.target.checked)}
                  />
                  启用
                </label>
                {s.origin && s.origin !== "bundled" && s.kind === "chat_skill" && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onUninstall(s)}
                    className="text-[10px] text-rose-300/80 hover:text-rose-300"
                    data-testid={`skill-uninstall-${s.id}`}
                  >
                    卸载
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
