/** Live project artifacts derived from Zustand payloads (Dim-1 — no backend). */

export type ArtifactKind =
  | "leaderboard"
  | "doe_plan"
  | "optimization"
  | "deep_report"
  | "loop_report";

export type ProjectArtifact = {
  /** Stable id — one live singleton per kind. */
  id: ArtifactKind;
  kind: ArtifactKind;
  title: string;
  subtitle: string;
  /** ActionsPanel modal name, or null when there is no dedicated modal. */
  modal: string | null;
  ready: boolean;
  busy: boolean;
};

export type ArtifactSource = {
  leaderboard: unknown[];
  formulationBusy: boolean;
  doePlan: { runs?: unknown[]; notes?: string } | null;
  busy: string;
  optimizationHistory: unknown[];
  deepReport: { citations?: unknown[] } | null;
  deepResearchBusy: boolean;
  loopReport: unknown | null;
};

const MODAL_BY_KIND: Record<ArtifactKind, string | null> = {
  leaderboard: "recommend",
  doe_plan: "doe",
  optimization: "optimize",
  deep_report: null,
  loop_report: "loop",
};

export function modalForArtifact(id: ArtifactKind): string | null {
  return MODAL_BY_KIND[id] ?? null;
}

/** Derive the current workspace artifact list from store fields. */
export function selectProjectArtifacts(s: ArtifactSource): ProjectArtifact[] {
  const out: ProjectArtifact[] = [];

  if (s.leaderboard.length > 0 || s.formulationBusy) {
    out.push({
      id: "leaderboard",
      kind: "leaderboard",
      title: "推荐配方",
      subtitle: s.formulationBusy
        ? "检索中…"
        : `${s.leaderboard.length} 条候选`,
      modal: MODAL_BY_KIND.leaderboard,
      ready: s.leaderboard.length > 0,
      busy: s.formulationBusy,
    });
  }

  if (s.doePlan || s.busy === "doe") {
    const n = s.doePlan?.runs?.length ?? 0;
    out.push({
      id: "doe_plan",
      kind: "doe_plan",
      title: "DOE 实验方案",
      subtitle: s.busy === "doe" ? "生成中…" : n > 0 ? `${n} 组实验` : (s.doePlan?.notes || "已生成"),
      modal: MODAL_BY_KIND.doe_plan,
      ready: Boolean(s.doePlan),
      busy: s.busy === "doe",
    });
  }

  if (s.optimizationHistory.length > 0 || s.busy === "optimizing") {
    out.push({
      id: "optimization",
      kind: "optimization",
      title: "寻优收敛",
      subtitle:
        s.busy === "optimizing"
          ? "寻优中…"
          : `${s.optimizationHistory.length} 轮历史`,
      modal: MODAL_BY_KIND.optimization,
      ready: s.optimizationHistory.length > 0,
      busy: s.busy === "optimizing",
    });
  }

  if (s.deepReport || s.deepResearchBusy) {
    const cites = s.deepReport?.citations?.length ?? 0;
    out.push({
      id: "deep_report",
      kind: "deep_report",
      title: "深度研究报告",
      subtitle: s.deepResearchBusy ? "生成中…" : cites > 0 ? `${cites} 条引用` : "已生成",
      modal: MODAL_BY_KIND.deep_report,
      ready: Boolean(s.deepReport),
      busy: s.deepResearchBusy,
    });
  }

  if (s.loopReport || s.busy === "looping") {
    out.push({
      id: "loop_report",
      kind: "loop_report",
      title: "自驱动闭环",
      subtitle: s.busy === "looping" ? "迭代中…" : "已完成一轮",
      modal: MODAL_BY_KIND.loop_report,
      ready: Boolean(s.loopReport),
      busy: s.busy === "looping",
    });
  }

  return out;
}
