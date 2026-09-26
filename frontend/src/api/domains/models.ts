// Domain facade: models (P2)
import { apiMethods } from "../methods";

export const modelsApi = {
  modelVersions: apiMethods.modelVersions,
  models: apiMethods.models,
  trainingStatus: apiMethods.trainingStatus,
} as const;

export type ModelsApi = typeof modelsApi;
