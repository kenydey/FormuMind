/**
 * 多会话聊天(2026-09-05 A1): 会话=前端编排 + 后端 /api/session 存储。
 *
 * Pinned semantics:
 *  - persistChatSession: 空对话不落库; 无 activeSessionId 时自动登记新会话;
 *    成功后 activeSessionId 更新 + 列表后台刷新。
 *  - switchChatSession: 切换前保存当前; load 结果替换 chatHistory。
 *  - newChatSession: 保存当前后清空对话, 回到传统模式(null)。
 *  - deleteChatSession: 删后端与本地列表; 删除的是当前会话则归零。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listSessions: vi.fn(),
  saveSession: vi.fn(),
  loadSession: vi.fn(),
  deleteSession: vi.fn(),
  scheduleAutosave: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listSessions: mocks.listSessions,
      saveSession: mocks.saveSession,
      loadSession: mocks.loadSession,
      deleteSession: mocks.deleteSession,
    },
  };
});

import { useStore } from "./index";

function seed(history: unknown[] = []) {
  useStore.setState({
    chatHistory: history as never,
    chatSessions: [],
    activeSessionId: null,
    chatSessionTitles: {},
    chatSessionsBusy: false,
    chatSessionsOpen: false,
    error: null,
    scheduleAutosave: mocks.scheduleAutosave,
  } as never);
}

const DONE_HISTORY = [
  { role: "user", content: "镁合金钝化机理?" },
  { role: "assistant", content: "植酸可在表面形成络合转化膜…" },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.saveSession.mockResolvedValue({ ok: true });
  mocks.listSessions.mockResolvedValue({ sessions: [], total_count: 0 });
  seed();
});

describe("refreshChatSessions", () => {
  it("按当前项目过滤会话列表(2026-09-05 入库项目)", async () => {
    mocks.listSessions.mockResolvedValue({ sessions: [], total_count: 0 });
    useStore.setState({ activeProjectId: "proj-xyz" } as never);
    await useStore.getState().refreshChatSessions();
    expect(mocks.listSessions).toHaveBeenCalledWith(50, "proj-xyz");
  });
});

describe("persistChatSession", () => {
  it("空对话不落库", async () => {
    await useStore.getState().persistChatSession();
    expect(mocks.saveSession).not.toHaveBeenCalled();
    expect(useStore.getState().activeSessionId).toBeNull();
  });

  it("无会话时自动登记新会话并保存过滤后的 history", async () => {
    seed([...DONE_HISTORY, { role: "assistant", content: "", streaming: true }]);
    useStore.setState({ activeProjectId: "proj-abc" } as never);
    await useStore.getState().persistChatSession();
    expect(mocks.saveSession).toHaveBeenCalledTimes(1);
    const [body] = mocks.saveSession.mock.calls[0];
    expect(body.session_id).toMatch(/^chat-\d+$/);
    expect(body.history).toHaveLength(2); // streaming 占位被剔除
    // 2026-09-05: 会话绑定项目 + 标题 = 首问(入库可调阅)
    expect(body.project_id).toBe("proj-abc");
    expect(body.title).toContain("镁合金");
    expect(useStore.getState().activeSessionId).toBe(body.session_id);
    expect(useStore.getState().chatSessionTitles[body.session_id]).toContain("镁合金");
  });

  it("已有会话时沿用同一 session_id", async () => {
    seed([...DONE_HISTORY]);
    useStore.setState({
      activeSessionId: "chat-1",
      chatSessionTitles: { "chat-1": "旧标题" },
    } as never);
    await useStore.getState().persistChatSession();
    expect(mocks.saveSession.mock.calls[0][0].session_id).toBe("chat-1");
    // 已有标题不覆盖
    expect(useStore.getState().chatSessionTitles["chat-1"]).toBe("旧标题");
  });
});

describe("switchChatSession", () => {
  it("切换前保存当前对话, load 结果载入 chatHistory", async () => {
    seed([...DONE_HISTORY]);
    useStore.setState({ activeSessionId: "chat-cur" } as never);
    mocks.loadSession.mockResolvedValue({
      history: [
        { role: "user", content: "另一线程的问题" },
        { role: "assistant", content: "另一线程的回答" },
      ],
      context: null,
    });

    await useStore.getState().switchChatSession("chat-other");

    expect(mocks.saveSession).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: "chat-cur" })
    );
    expect(useStore.getState().activeSessionId).toBe("chat-other");
    expect(useStore.getState().chatHistory.map((m) => m.content)).toEqual([
      "另一线程的问题",
      "另一线程的回答",
    ]);
  });

  it("同会话切换是 no-op", async () => {
    useStore.setState({ activeSessionId: "chat-x" } as never);
    await useStore.getState().switchChatSession("chat-x");
    expect(mocks.saveSession).not.toHaveBeenCalled();
    expect(mocks.loadSession).not.toHaveBeenCalled();
  });
});

describe("newChatSession / deleteChatSession", () => {
  it("new 保存当前并清空回到传统模式", async () => {
    seed([...DONE_HISTORY]);
    useStore.setState({ activeSessionId: "chat-a" } as never);
    await useStore.getState().newChatSession();
    expect(mocks.saveSession).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: "chat-a" })
    );
    expect(useStore.getState().chatHistory).toEqual([]);
    expect(useStore.getState().activeSessionId).toBeNull();
  });

  it("delete 移除本地列表; 删除当前会话则归零", async () => {
    useStore.setState({
      chatSessions: [{ session_id: "chat-1", history_count: 2 }],
      activeSessionId: "chat-1",
      chatSessionTitles: { "chat-1": "t" },
    } as never);
    mocks.deleteSession.mockResolvedValue({ ok: true });

    await useStore.getState().deleteChatSession("chat-1");

    expect(useStore.getState().chatSessions).toEqual([]);
    expect(useStore.getState().activeSessionId).toBeNull();
    expect(useStore.getState().chatSessionTitles).toEqual({});
  });
});
