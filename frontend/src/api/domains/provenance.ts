// Domain facade: provenance (W3-10)
import { apiMethods } from "../methods";

export const provenanceApi = {
  getProvenanceLineage: apiMethods.getProvenanceLineage,
} as const;

export type ProvenanceApi = typeof provenanceApi;
