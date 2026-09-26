// Domain facade: doe (P2)
import { apiMethods } from "../methods";

export const doeApi = {
  activeDoe: apiMethods.activeDoe,
  doe: apiMethods.doe,
  doeExportUrl: apiMethods.doeExportUrl,
  getDoeCycleStatus: apiMethods.getDoeCycleStatus,
  listDoeHistory: apiMethods.listDoeHistory,
  postDoeCyclePause: apiMethods.postDoeCyclePause,
  startDoeCycle: apiMethods.startDoeCycle,
  suggestFactors: apiMethods.suggestFactors,
} as const;

export type DoeApi = typeof doeApi;
