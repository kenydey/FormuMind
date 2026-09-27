// Domain facade: review runs (W5-4 / P1-28) — read review-run audits,
// stale status, and manual re-run.
import { apiMethods } from "../methods";

export const reviewsApi = {
  listReviewRuns: apiMethods.listReviewRuns,
  getReviewRun: apiMethods.getReviewRun,
  rerunReviewRun: apiMethods.rerunReviewRun,
} as const;

export type ReviewsApi = typeof reviewsApi;
