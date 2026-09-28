// FormuMind frontend API client — split barrel (P2).
export * from "./types";
export {
  ApiError,
  BACKEND_UNREACHABLE_MESSAGE,
  apiAuthHeaders,
  formatApiError,
  get,
  getApiToken,
  isBackendUnreachableError,
  jsonHeaders,
  post,
  postAccepted,
  put,
  del,
  readApiError,
  sanitizeEvidenceForApi,
  setApiToken,
} from "./http";
export { apiMethods } from "./methods";
export * from "./extras";
export { reviewsApi } from "./domains/reviews";

import { apiMethods } from "./methods";

/** Backward-compatible monolithic client (same surface as pre-split api.ts). */
export const api = apiMethods;
