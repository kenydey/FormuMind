// Domain facade: experiments (P2)
import { apiMethods } from "../methods";

export const experimentsApi = {
  deleteWorkbenchAttachment: apiMethods.deleteWorkbenchAttachment,
  experimentMeasurements: apiMethods.experimentMeasurements,
  getWorkbenchAttachments: apiMethods.getWorkbenchAttachments,
  getWorkbenchCampaign: apiMethods.getWorkbenchCampaign,
  getWorkbenchQuality: apiMethods.getWorkbenchQuality,
  getWorkbenchRowMeasurements: apiMethods.getWorkbenchRowMeasurements,
  getWorkbenchVersions: apiMethods.getWorkbenchVersions,
  listCampaignRounds: apiMethods.listCampaignRounds,
  reconcileWorkbench: apiMethods.reconcileWorkbench,
  syncWorkbench: apiMethods.syncWorkbench,
  workbenchAttachmentDownloadUrl: apiMethods.workbenchAttachmentDownloadUrl,
} as const;

export type ExperimentsApi = typeof experimentsApi;
