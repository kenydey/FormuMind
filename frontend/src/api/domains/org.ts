// Domain facade: org (P2)
import { apiMethods } from "../methods";

export const orgApi = {
  orgDashboard: apiMethods.orgDashboard,
} as const;

export type OrgApi = typeof orgApi;
