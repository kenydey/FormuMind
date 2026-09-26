// Domain facade: search (P2)
import { apiMethods } from "../methods";

export const searchApi = {
  search: apiMethods.search,
  searchExperiments: apiMethods.searchExperiments,
  searchStream: apiMethods.searchStream,
} as const;

export type SearchApi = typeof searchApi;
