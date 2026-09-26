// Domain facade: meta (P2)
import { apiMethods } from "../methods";

export const metaApi = {
  getMeta: apiMethods.getMeta,
} as const;

export type MetaApi = typeof metaApi;
