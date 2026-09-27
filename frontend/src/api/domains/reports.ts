// Domain facade: reports (W3-13)
import { apiMethods } from "../methods";

export const reportsApi = {
  exportTechReport: apiMethods.exportTechReport,
  getReportCapabilities: apiMethods.getReportCapabilities,
} as const;

export type ReportsApi = typeof reportsApi;
