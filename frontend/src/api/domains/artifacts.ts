// Domain facade: artifact versions (W4-1/W4-4) — immutable version lineage,
// copy-on-write restore, and version diff.
import { apiMethods } from "../methods";

export const artifactsApi = {
  createArtifactLineage: apiMethods.createArtifactLineage,
  listArtifactVersions: apiMethods.listArtifactVersions,
  restoreArtifactVersion: apiMethods.restoreArtifactVersion,
  getArtifactVersionDiff: apiMethods.getArtifactVersionDiff,
} as const;

export type ArtifactsApi = typeof artifactsApi;
