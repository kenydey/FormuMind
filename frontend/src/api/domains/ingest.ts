// Domain facade: ingest (P2)
import { apiMethods } from "../methods";

export const ingestApi = {
  ingest: apiMethods.ingest,
  ingestBatch: apiMethods.ingestBatch,
  ingestEvidence: apiMethods.ingestEvidence,
  ingestTask: apiMethods.ingestTask,
  ingestText: apiMethods.ingestText,
  ingestUrl: apiMethods.ingestUrl,
  uploadAttachment: apiMethods.uploadAttachment,
  uploadProjectExport: apiMethods.uploadProjectExport,
  uploadQcReport: apiMethods.uploadQcReport,
  uploadStructure: apiMethods.uploadStructure,
  uploadWorkbenchAttachment: apiMethods.uploadWorkbenchAttachment,
} as const;

export type IngestApi = typeof ingestApi;
