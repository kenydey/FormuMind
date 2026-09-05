/**
 * 多会话聊天(2026-09-05 A1)。
 *
 * 会话 = 前端状态 + 后端 /api/session 存储 API(Redis, TTL 24h)的编排层:
 *  - 后端 session 端点此前无任何前端消费(死代码缺口), chat 链路只靠前端
 *    workspace.chat_history 单线; 本 slice 让一个项目内可并行多个对话线程。
 *  - 每轮问答完成(done)由 sendChat 调 persistChatSession() 自动落库。
 *  - activeSessionId = null 时为传统模式(仅 project workspace 持久, 不写后端)。
 */
import { api, type ChatMessage } from "../../api";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

function sessionTitleFromHistory(history: ChatMessage[]): string {
  const first = history.find((m) => m.role === "user");
  const text = first?.content?.trim() ?? "";
  return text.length > 28 ? `${text.slice(0, 28)}…` : text;
}

function nowId(): string {
  return `chat-${Date.now()}`;
}

function serializeHistory(history: ChatMessage[]): Record<string, unknown>[] {
  return history
    .filter((m) => !m.streaming && m.content)
    .map((m) => ({
      role: m.role,
      content: m.content,
      citations: m.citations ?? undefined,
      phase: m.phase ?? undefined,
    }));
}

export function createChatSessionSlice(set: SliceSet, get: SliceGet) {
  return {
    setChatSessionsOpen: (open: boolean) =>
      set((draft) => {
        draft.chatSessionsOpen = open;
      }),

    refreshChatSessions: async () => {
      try {
        const { activeProjectId } = get();
        // 2026-09-05: 会话入库项目 → 列表按项目过滤(切项目只见此项目会话)
        const res = await api.listSessions(50, activeProjectId ?? undefined);
        set((draft) => {
          draft.chatSessions = res.sessions ?? [];
        });
      } catch {
        // 后端不可达时不打扰会话 UI
      }
    },

    /** 保存当前对话并开启全新会话(activeSessionId = null: 暂不落后端,
     *  用户首问完成即自动建会话)。 */
    newChatSession: async () => {
      const { chatHistory, activeSessionId, chatSessionTitles } = get();
      const nonEmpty = chatHistory.some((m) => !m.streaming && m.content);
      if (activeSessionId && nonEmpty) {
        await get().persistChatSession();
      }
      set((draft) => {
        draft.activeSessionId = null;
        draft.chatHistory = [];
        draft.chatSessionTitles = { ...chatSessionTitles };
      });
      get().scheduleAutosave();
    },

    switchChatSession: async (sessionId: string) => {
      const { chatHistory, activeSessionId } = get();
      if (sessionId === activeSessionId) return;
      const nonEmpty = chatHistory.some((m) => !m.streaming && m.content);
      if (activeSessionId && nonEmpty) {
        await get().persistChatSession();
      }
      set((draft) => {
        draft.chatSessionsBusy = true;
      });
      try {
        const loaded = await api.loadSession(sessionId);
        const history = (loaded.history ?? []) as unknown as ChatMessage[];
        set((draft) => {
          draft.chatHistory = Array.isArray(history)
            ? history.filter(
                (m): m is ChatMessage =>
                  !!m && typeof m.content === "string"
              )
            : [];
          draft.activeSessionId = sessionId;
          if (!draft.chatSessionTitles[sessionId]) {
            draft.chatSessionTitles[sessionId] = sessionTitleFromHistory(
              draft.chatHistory
            );
          }
        });
        get().scheduleAutosave();
      } catch (e) {
        set((draft) => {
          draft.error = e instanceof Error ? e.message : String(e);
        });
      } finally {
        set((draft) => {
          draft.chatSessionsBusy = false;
        });
      }
    },

    deleteChatSession: async (sessionId: string) => {
      const { activeSessionId } = get();
      try {
        await api.deleteSession(sessionId);
      } catch {
        // 后端无此会话时忽略
      }
      set((draft) => {
        draft.chatSessions = draft.chatSessions.filter((s) => s.session_id !== sessionId);
        const titles = { ...draft.chatSessionTitles };
        delete titles[sessionId];
        draft.chatSessionTitles = titles;
        if (draft.activeSessionId === sessionId) {
          draft.activeSessionId = null;
        }
      });
      // 删除的是当前会话 → 传统模式(对话仍在 workspace chat_history)
      if (activeSessionId === sessionId) {
        get().scheduleAutosave();
      }
    },

    /** 当前会话落库; 无 activeSessionId 且对话非空时自动登记新会话。 */
    persistChatSession: async () => {
      const { chatHistory, activeSessionId } = get();
      const nonEmpty = chatHistory.some((m) => !m.streaming && m.content);
      if (!nonEmpty) return;
      const sessionId = activeSessionId ?? nowId();
      const payload = serializeHistory(chatHistory);
      const { activeProjectId } = get();
      const firstUser = chatHistory.find((m) => m.role === "user")?.content ?? "";
      await api.saveSession({
        session_id: sessionId,
        history: payload,
        ttl_seconds: 7 * 86400,
        // 2026-09-05: 会话绑定项目, 可调阅(title = 首个用户问句)
        project_id: activeProjectId ?? undefined,
        title: firstUser.slice(0, 80),
      }).catch(() => null);
      set((draft) => {
        draft.activeSessionId = sessionId;
        if (!draft.chatSessionTitles[sessionId]) {
          draft.chatSessionTitles[sessionId] = sessionTitleFromHistory(chatHistory);
        }
      });
      // 后台刷新列表(不阻塞)
      void get().refreshChatSessions();
    },
  } as Pick<
    AppState,
    | "setChatSessionsOpen"
    | "refreshChatSessions"
    | "newChatSession"
    | "switchChatSession"
    | "deleteChatSession"
    | "persistChatSession"
  >;
}
