// Domain facade: session (P2)
import { apiMethods } from "../methods";

export const sessionApi = {
  deleteSession: apiMethods.deleteSession,
  listSessions: apiMethods.listSessions,
  loadSession: apiMethods.loadSession,
  saveSession: apiMethods.saveSession,
  sessionInfo: apiMethods.sessionInfo,
} as const;

export type SessionApi = typeof sessionApi;
