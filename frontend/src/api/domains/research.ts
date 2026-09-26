// Domain facade: research (P2)
import { apiMethods } from "../methods";

export const researchApi = {
  research: apiMethods.research,
} as const;

export type ResearchApi = typeof researchApi;
