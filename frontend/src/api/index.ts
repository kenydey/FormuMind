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
export { chatApi } from "./domains/chat";
export { chemistryApi } from "./domains/chemistry";
export { doeApi } from "./domains/doe";
export { experimentsApi } from "./domains/experiments";
export { formulationsApi } from "./domains/formulations";
export { ingestApi } from "./domains/ingest";
export { kbApi } from "./domains/kb";
export { kgApi } from "./domains/kg";
export { loopApi } from "./domains/loop";
export { materialsApi } from "./domains/materials";
export { metaApi } from "./domains/meta";
export { miscApi } from "./domains/misc";
export { modelsApi } from "./domains/models";
export { notebooklmApi } from "./domains/notebooklm";
export { orgApi } from "./domains/org";
export { projectsApi } from "./domains/projects";
export { researchApi } from "./domains/research";
export { searchApi } from "./domains/search";
export { sessionApi } from "./domains/session";
export { surechemblApi } from "./domains/surechembl";
export { tasksApi } from "./domains/tasks";
export { wikiApi } from "./domains/wiki";

import { apiMethods } from "./methods";

/** Backward-compatible monolithic client (same surface as pre-split api.ts). */
export const api = apiMethods;
