// Domain facade: chat (P2)
import { apiMethods } from "../methods";

export const chatApi = {
  chat: apiMethods.chat,
  chatStream: apiMethods.chatStream,
} as const;

export type ChatApi = typeof chatApi;
