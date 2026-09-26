// Domain facade: loop (P2)
import { apiMethods } from "../methods";

export const loopApi = {
  loopIterate: apiMethods.loopIterate,
} as const;

export type LoopApi = typeof loopApi;
