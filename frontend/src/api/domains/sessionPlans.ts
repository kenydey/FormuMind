// Domain facade: session plans (W3-9)
import { apiMethods } from "../methods";

export const sessionPlansApi = {
  getSessionPlan: apiMethods.getSessionPlan,
  decideSessionPlan: apiMethods.decideSessionPlan,
  listPendingSessionPlans: apiMethods.listPendingSessionPlans,
} as const;

export type SessionPlansApi = typeof sessionPlansApi;
