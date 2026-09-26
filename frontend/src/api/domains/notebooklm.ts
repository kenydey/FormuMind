// Domain facade: notebooklm (P2)
import { apiMethods } from "../methods";

export const notebooklmApi = {
  notebooklmConfig: apiMethods.notebooklmConfig,
  notebooklmLogin: apiMethods.notebooklmLogin,
  notebooklmStatus: apiMethods.notebooklmStatus,
} as const;

export type NotebooklmApi = typeof notebooklmApi;
