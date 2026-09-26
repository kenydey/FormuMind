// Domain facade: chemistry (P2)
import { apiMethods } from "../methods";

export const chemistryApi = {
  chemicalLookup: apiMethods.chemicalLookup,
  chemicalProfile: apiMethods.chemicalProfile,
  chemicalTools: apiMethods.chemicalTools,
} as const;

export type ChemistryApi = typeof chemistryApi;
