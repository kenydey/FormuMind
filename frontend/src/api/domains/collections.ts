// Domain facade: smart collections (W6-3 / P2-3) — saved query+filter sets
// with scheduled refresh and snapshot diffs.
import { del, get, patch, post } from "../http";

export type CollectionFilters = {
  date_from?: string | number;
  date_to?: string | number;
  domain_allowlist?: string[];
};

export type CollectionSchedule = {
  enabled: boolean;
  interval_hours: number;
  last_run?: number | null;
};

export type CollectionSummary = {
  collection_id: string;
  name: string;
  query: string;
  filters: CollectionFilters;
  screening_preset?: string | null;
  schedule: CollectionSchedule;
  created_at?: number;
  updated_at?: number;
  snapshot_count: number;
  last_snapshot?: {
    snapshot_id: string;
    at: number;
    total: number;
    added: number;
    removed: number;
  } | null;
};

export type CollectionSnapshot = {
  snapshot_id: string;
  at: number;
  actor?: string;
  query: string;
  filters: CollectionFilters;
  item_ids: string[];
  added: string[];
  removed: string[];
  total: number;
  manifest_added: number;
  manifest_skipped_existing: number;
};

export type CollectionDetail = Omit<CollectionSummary, "last_snapshot"> & {
  snapshots: CollectionSnapshot[];
};

export type CollectionCreateInput = {
  name: string;
  query: string;
  filters?: CollectionFilters;
  screening_preset?: string;
  schedule?: { enabled: boolean; interval_hours: number };
};

function withProject(path: string, projectId: string): string {
  const qs = new URLSearchParams({ project_id: projectId });
  return `${path}?${qs}`;
}

export const collectionsApi = {
  list: (projectId: string): Promise<{ collections: CollectionSummary[] }> =>
    get(withProject("/api/collections", projectId)),
  create: (projectId: string, input: CollectionCreateInput): Promise<CollectionDetail> =>
    post("/api/collections", { project_id: projectId, ...input }),
  detail: (projectId: string, id: string): Promise<CollectionDetail> =>
    get(withProject(`/api/collections/${id}`, projectId)),
  update: (
    projectId: string,
    id: string,
    input: Partial<CollectionCreateInput>,
  ): Promise<CollectionDetail> =>
    patch(withProject(`/api/collections/${id}`, projectId), input),
  remove: (projectId: string, id: string): Promise<{ deleted: boolean }> =>
    del(withProject(`/api/collections/${id}`, projectId)),
  refresh: (projectId: string, id: string): Promise<CollectionSnapshot> =>
    post(withProject(`/api/collections/${id}/refresh`, projectId), {}),
} as const;

export type CollectionsApi = typeof collectionsApi;
