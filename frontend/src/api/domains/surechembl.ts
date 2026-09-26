// Domain facade: surechembl (P2)
import { apiMethods } from "../methods";

export const surechemblApi = {
  surechemblConfirmExampleDraft: apiMethods.surechemblConfirmExampleDraft,
  surechemblExtractExampleDraft: apiMethods.surechemblExtractExampleDraft,
  surechemblIngestDocument: apiMethods.surechemblIngestDocument,
} as const;

export type SurechemblApi = typeof surechemblApi;
