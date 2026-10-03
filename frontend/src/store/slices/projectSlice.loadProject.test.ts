/**
 * Switching projects must not throw away the edits made in the one being left.
 *
 * ``loadProject`` raised ``projectLoading`` and cancelled the pending autosave
 * *before* calling ``saveProject`` — whose first line is "if (projectLoading)
 * return" (the guard that stops an empty, still-loading workspace from being
 * written back). So the "save the project we are leaving" step was a no-op and
 * anything edited in the last AUTOSAVE_MS (1.5 s) before clicking another project
 * in the history list was lost.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  updateProject: vi.fn(),
  getProject: vi.fn(),
  listProjects: vi.fn(),
  createProject: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      updateProject: mocks.updateProject,
      getProject: mocks.getProject,
      listProjects: mocks.listProjects,
      createProject: mocks.createProject,
      syncDefaultLevers: vi.fn(),
      workbenchStats: vi.fn(),
    },
  };
});

import { useStore } from "../index";

function seed(over: Record<string, unknown> = {}) {
  useStore.setState({
    activeProjectId: "A",
    projectLoading: false,
    projectSaveBusy: false,
    searchQuery: "edited in project A",
    error: null,
    syncDefaultLevers: vi.fn(async () => {}),
    refreshWorkbenchStats: vi.fn(async () => {}),
    ...over,
  } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.updateProject.mockResolvedValue({});
  mocks.listProjects.mockResolvedValue([]);
  mocks.getProject.mockResolvedValue({ id: "B", title: "B", workspace: {} });
  seed();
});

describe("loadProject", () => {
  it("saves the project being left BEFORE fetching the next one", async () => {
    await useStore.getState().loadProject("B");

    expect(mocks.updateProject).toHaveBeenCalledTimes(1);
    const [savedId, payload] = mocks.updateProject.mock.calls[0];
    expect(savedId).toBe("A");
    expect((payload as { search_query: string }).search_query).toBe("edited in project A");
    expect(mocks.updateProject.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.getProject.mock.invocationCallOrder[0],
    );
    expect(useStore.getState().activeProjectId).toBe("B");
  });

  it("saves even when a debounced autosave was still pending (the typical case)", async () => {
    vi.useFakeTimers();
    try {
      // saveProject skips content identical to the last save (module-level dedupe),
      // so this test needs its own edit.
      seed({ searchQuery: "second edit, autosave still pending" });
      useStore.getState().scheduleAutosave(); // pending, would fire in 1.5 s
      await useStore.getState().loadProject("B");
      expect(mocks.updateProject).toHaveBeenCalledTimes(1);
      // the cancelled autosave must not fire a second, stale save later
      await vi.advanceTimersByTimeAsync(5000);
      expect(mocks.updateProject).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not save when re-opening the already active project", async () => {
    await useStore.getState().loadProject("A");
    expect(mocks.updateProject).not.toHaveBeenCalled();
  });

  it("does not save when there is no active project yet", async () => {
    seed({ activeProjectId: null });
    await useStore.getState().loadProject("B");
    expect(mocks.updateProject).not.toHaveBeenCalled();
  });

  it("still loads the next project when saving the old one fails, and says so", async () => {
    mocks.updateProject.mockRejectedValue(new Error("backend down"));
    await useStore.getState().loadProject("B");
    expect(useStore.getState().activeProjectId).toBe("B");
  });

  it("keeps the autosave guard: nothing is saved while a load is in flight", async () => {
    seed({ projectLoading: true });
    await useStore.getState().saveProject();
    expect(mocks.updateProject).not.toHaveBeenCalled();
  });
});

describe("saveProject dirty check", () => {
  it("saves a change confined to a field the old fingerprint ignored (search query, ticked sources)", async () => {
    seed({ searchQuery: "first query", selectedSources: [] });
    await useStore.getState().saveProject();
    expect(mocks.updateProject).toHaveBeenCalledTimes(1);

    seed({ searchQuery: "second query", selectedSources: [] });
    await useStore.getState().saveProject();
    expect(mocks.updateProject).toHaveBeenCalledTimes(2);

    seed({ searchQuery: "second query", selectedSources: ["US123"] });
    await useStore.getState().saveProject();
    expect(mocks.updateProject).toHaveBeenCalledTimes(3);
  });

  it("still skips the PUT when nothing changed", async () => {
    seed({ searchQuery: "unchanged" });
    await useStore.getState().saveProject();
    await useStore.getState().saveProject();
    expect(mocks.updateProject).toHaveBeenCalledTimes(1);
  });
});


describe("loadProject — the empty-payload safeguard must not leak one project into another", () => {
  const srcA = { identifier: "US-A", title: "A's patent", source: "patent" };
  const msgA = { role: "user", content: "question asked in project A" };

  it("a project whose saved workspace is empty comes up empty, not with the previous project's sources/chat", async () => {
    seed({ sources: [srcA], chatHistory: [msgA] });
    mocks.getProject.mockResolvedValue({ id: "B", title: "B", workspace: {} });

    await useStore.getState().loadProject("B");

    const s = useStore.getState();
    expect(s.activeProjectId).toBe("B");
    // The safeguard ("server sources empty but the local mirror has some → keep showing
    // them") was written for re-opening the SAME project after a glitchy empty payload.
    // It compared against whatever workspace was in memory — i.e. project A's — so a
    // brand-new project B inherited A's sources and chat, and the next autosave wrote them
    // into B.
    expect(s.sources).toEqual([]);
    expect(s.chatHistory).toEqual([]);
  });

  it("still restores the local mirror when the SAME project comes back empty", async () => {
    seed({ sources: [srcA], chatHistory: [msgA] });
    mocks.getProject.mockResolvedValue({ id: "A", title: "A", workspace: {} });

    await useStore.getState().loadProject("A");

    const s = useStore.getState();
    expect(s.sources).toEqual([srcA]);
    expect(s.chatHistory).toEqual([msgA]);
  });
});

describe("loadProject — per-project chat session list", () => {
  it("drops the previous project's session list and refetches it when the drawer is open", async () => {
    const refresh = vi.fn(async () => {});
    seed({
      chatSessions: [{ session_id: "sess-of-A", title: "A's thread" }],
      chatSessionTitles: { "sess-of-A": "A's thread" },
      chatSessionsOpen: true,
      refreshChatSessions: refresh,
    });
    await useStore.getState().loadProject("B");

    const s = useStore.getState();
    expect(s.chatSessions).toEqual([]);
    expect(s.chatSessionTitles).toEqual({});
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("does not refetch while the drawer is closed (it fetches when opened)", async () => {
    const refresh = vi.fn(async () => {});
    seed({ chatSessionsOpen: false, refreshChatSessions: refresh });
    await useStore.getState().loadProject("B");
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe("switching projects clears the previous project's transient working state", () => {
  const dirty = {
    sourceStatus: { patents: { ok: false, error: "A: rate limited" } },
    usedSeedFallback: true,
    filterReport: { kept: 3 },
    uploadWarnings: ["A: scanned PDF"],
    relationInsights: [{ relation: "x" }],
    formulationValidateWarnings: ["A: weights sum to 68%"],
    deepResearchStage: "report",
    recommendMessage: "A finished",
  };

  it("loadProject", async () => {
    seed(dirty);
    await useStore.getState().loadProject("B");
    const s = useStore.getState();
    expect(s.sourceStatus).toEqual({});
    expect(s.usedSeedFallback).toBe(false);
    expect(s.filterReport).toBeNull();
    expect(s.uploadWarnings).toEqual([]);
    expect(s.relationInsights).toEqual([]);
    expect(s.formulationValidateWarnings).toEqual([]);
    expect(s.deepResearchStage).toBe("");
    expect(s.recommendMessage).toBe("");
  });

  it("createProject", async () => {
    mocks.createProject.mockResolvedValue({ id: "C", title: "C", workspace: {} });
    seed(dirty);
    await useStore.getState().createProject("C");
    const s = useStore.getState();
    expect(s.activeProjectId).toBe("C");
    expect(s.sourceStatus).toEqual({});
    expect(s.filterReport).toBeNull();
    expect(s.uploadWarnings).toEqual([]);
    expect(s.formulationValidateWarnings).toEqual([]);
  });
});
