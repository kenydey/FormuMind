// Domain facade: kb (P2)
import { apiMethods } from "../methods";

export const kbApi = {
  archiveKbSource: apiMethods.archiveKbSource,
  deleteKbSource: apiMethods.deleteKbSource,
  kbChunksBySource: apiMethods.kbChunksBySource,
  kbGoldenEvalRun: apiMethods.kbGoldenEvalRun,
  kbGoldenQuestions: apiMethods.kbGoldenQuestions,
  kbHybridSearch: apiMethods.kbHybridSearch,
  kbIntegrity: apiMethods.kbIntegrity,
  kbProducts: apiMethods.kbProducts,
  kbQualityOps: apiMethods.kbQualityOps,
  kbQueryTest: apiMethods.kbQueryTest,
  kbReindex: apiMethods.kbReindex,
  kbRetentionPurge: apiMethods.kbRetentionPurge,
  kbRetrievalSettings: apiMethods.kbRetrievalSettings,
  kbSearch: apiMethods.kbSearch,
  kbSources: apiMethods.kbSources,
  kbStats: apiMethods.kbStats,
} as const;

export type KbApi = typeof kbApi;
