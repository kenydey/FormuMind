// Domain facade: review runs (W5-4 / P1-28) — read review-run audits,
// stale status, manual re-run, and the publication checklist (P1-32).
import { apiMethods } from "../methods";

export const reviewsApi = {
  listReviewRuns: apiMethods.listReviewRuns,
  getReviewRun: apiMethods.getReviewRun,
  rerunReviewRun: apiMethods.rerunReviewRun,
  getReviewChecklist: apiMethods.getReviewChecklist,
} as const;
