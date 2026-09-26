// Domain facade: kg (P2)
import { apiMethods } from "../methods";

export const kgApi = {
  kgCalibration: apiMethods.kgCalibration,
  kgContradictions: apiMethods.kgContradictions,
  kgFeedbackReport: apiMethods.kgFeedbackReport,
  kgFeedbackStats: apiMethods.kgFeedbackStats,
  kgGraph: apiMethods.kgGraph,
  kgLinkSource: apiMethods.kgLinkSource,
  kgPath: apiMethods.kgPath,
  kgRebuild: apiMethods.kgRebuild,
  kgRelations: apiMethods.kgRelations,
  kgRelationsRebuild: apiMethods.kgRelationsRebuild,
  kgResolve: apiMethods.kgResolve,
  kgRetrieve: apiMethods.kgRetrieve,
  kgSimilarFormulations: apiMethods.kgSimilarFormulations,
  kgStats: apiMethods.kgStats,
  kgSubstitutes: apiMethods.kgSubstitutes,
  neo4jCompoundSimilar: apiMethods.neo4jCompoundSimilar,
  neo4jCompounds: apiMethods.neo4jCompounds,
  neo4jEnsureSchema: apiMethods.neo4jEnsureSchema,
  neo4jFormulationCompounds: apiMethods.neo4jFormulationCompounds,
  neo4jFormulations: apiMethods.neo4jFormulations,
  neo4jLinkContains: apiMethods.neo4jLinkContains,
  neo4jLinkSimilar: apiMethods.neo4jLinkSimilar,
  neo4jStats: apiMethods.neo4jStats,
  neo4jUpsertCompound: apiMethods.neo4jUpsertCompound,
  neo4jUpsertFormulation: apiMethods.neo4jUpsertFormulation,
} as const;

export type KgApi = typeof kgApi;
