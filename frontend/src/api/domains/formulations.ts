// Domain facade: formulations (P2)
import { apiMethods } from "../methods";

export const formulationsApi = {
  formulationLineage: apiMethods.formulationLineage,
  getFormulationMode: apiMethods.getFormulationMode,
  getFormulationSkill: apiMethods.getFormulationSkill,
  listFormulationSkills: apiMethods.listFormulationSkills,
  recommendFormulations: apiMethods.recommendFormulations,
} as const;

export type FormulationsApi = typeof formulationsApi;
