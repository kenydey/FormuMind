// API methods object (P2: kept cohesive; domain facades in ./domains).
import {
  apiAuthHeaders,
  del,
  get,
  jsonHeaders,
  post,
  postAccepted,
  put,
  readApiError,
} from "./http";
import { awaitTaskStream } from "./extras";
import type {
  ActiveDoeResult,
  Attachment,
  BatchUpdateRequest,
  ChatRequest,
  ChatResponse,
  ChatStreamEvent,
  ChemToolsStatus,
  ChemicalHit,
  ChemicalProfile,
  DOEPlan,
  DependencyListResponse,
  EmbodimentDraft,
  EmbodimentEligibilityItem,
  EnvFlag,
  Evidence,
  ExperimentRecord,
  ExperimentSearchHit,
  ExperimentSummary,
  FactorCandidate,
  Formulation,
  FormulationSkill,
  FormulationVersionView,
  IPAnalysisRequest,
  IPReport,
  IngestResponse,
  IntentResult,
  KBQualityOps,
  KBReindexResult,
  KBSourcesResponse,
  KBStats,
  KGContradictionResponse,
  KGEntityResolveResponse,
  KGRelationView,
  KGSubstituteDiscoverResponse,
  KbChunk,
  KbGoldenEvalRequest,
  KbGoldenEvalResponse,
  KbGoldenQuestion,
  KbIntegrityResponse,
  KbProductsResponse,
  KbQueryTestRequest,
  KbQueryTestResponse,
  KbRetrievalSettings,
  KbSearchChunk,
  KgCalibrationResponse,
  KgLinkReport,
  KgMaterialGraphResponse,
  KgPathResponse,
  KgRebuildReport,
  KgRetrieveResponse,
  KgStats,
  LLMConfig,
  LLMSettingsResponse,
  LeverSpec,
  LlmModelsRefreshResponse,
  MaterialCandidate,
  MaterialImportPreview,
  MaterialListResponse,
  MaterialSpec,
  MaterialView,
  ModelInfo,
  ModelVersionMeta,
  Neo4jCompound,
  Neo4jFormulation,
  Neo4jHit,
  Neo4jLinkResponse,
  Neo4jStats,
  NotebookLMLoginResult,
  NotebookLMStatus,
  ObjectiveSpec,
  OcsrStatus,
  OptimizationResult,
  OrgDashboardStats,
  PlatformHealth,
  ProductDomain,
  ProjectDetailResponse,
  ProjectExportFile,
  ProjectPayloadHistoryResponse,
  QCMeasurementView,
  QCReportResult,
  RecommendFormulationsResponse,
  ReconcileResult,
  Requirement,
  ResearchResult,
  RetentionPurgeResult,
  SearchRequest,
  SearchResponse,
  SecretsListResponse,
  SessionInfoResponse,
  SessionListResponse,
  SessionLoadResponse,
  SimilarFormulationResponse,
  SourceStatus,
  StructureRecognitionResult,
  SubstitutionReport,
  SupplyRiskReport,
  SurechemblExampleDraft,
  TargetSpec,
  TaskStatus,
  TrainingReport,
  TrainingStatus,
  VersionDiffResult,
  VisionProbeResult,
  VisionSettings,
  VisionSettingsUpdate,
  WikiFlagsResponse,
  WikiPageDetail,
  WikiPageGraphResponse,
  WikiPagesResponse,
  WikiSearchResponse,
  WorkbenchCampaignResponse,
  WorkbenchCampaignSummary,
  WorkbenchQuality,
  WorkbenchRow,
  WorkbenchSyncResponse
} from "./types";

export const apiMethods = {
  research: (req: Requirement, sources: Evidence[] = [], query = "") =>
    post<ResearchResult>("/api/research", { ...req, sources, query }),
  recommendFormulations: (
    req: Requirement,
    objectives?: ObjectiveSpec[],
    sources: Evidence[] = [],
    n = 3,
    opts: { preferMaterialsCatalog?: boolean; relationInsight?: boolean } = {}
  ) =>
    post<RecommendFormulationsResponse>("/api/formulations/recommend", {
      requirement: req,
      objectives: objectives ?? req.objectives,
      sources,
      n,
      prefer_materials_catalog: Boolean(opts.preferMaterialsCatalog),
      relation_insight: opts.relationInsight !== false,
    }),
  chemicalLookup: (q: string) =>
    get<{
      query: string;
      cas: string;
      iupac_name: string;
      zh_name: string;
      formula: string;
      smiles?: string | null;
      molar_mass?: number | null;
      found: boolean;
      source: string;
      providers_tried?: string[];
      surechembl?: {
        chemical_id?: string | null;
        global_frequency?: number | null;
        inchi_key?: string | null;
        source_url?: string | null;
        alternates?: Array<{
          chemical_id?: string | null;
          name?: string | null;
          smiles?: string | null;
          global_frequency?: number | null;
        }>;
      };
      suppliers?: Array<{
        name: string;
        url?: string | null;
        product_url?: string | null;
      }>;
    }>(`/api/chemical/lookup?q=${encodeURIComponent(q)}`),
  chemicalProfile: (q: string) =>
    get<ChemicalProfile>(`/api/chemical/profile?q=${encodeURIComponent(q)}`),
  chemicalTools: () => get<ChemToolsStatus>("/api/chemical/tools"),
  addManualFormulation: (formulation: Formulation, requirement?: Requirement) =>
    post<{ formulation: Formulation; warnings: string[] }>("/api/formulations/manual", {
      formulation,
      requirement: requirement ?? null,
    }),
  validateFormulations: (formulations: Formulation[], requirement?: Requirement | null) =>
    post<{ formulations: Formulation[]; warnings: string[] }>("/api/formulations/validate", {
      formulations,
      requirement: requirement ?? null,
    }),
  modifyFormulations: (
    req: Requirement,
    modifyPrompt: string,
    opts: {
      sources?: Evidence[];
      baseFormulas?: Formulation[];
      baseFormulation?: Formulation;
      query?: string;
      n?: number;
    } = {}
  ) =>
    postAccepted("/api/research/modify", {
      requirement: req,
      modify_prompt: modifyPrompt,
      sources: opts.sources ?? [],
      base_formulas: opts.baseFormulas ?? (opts.baseFormulation ? [opts.baseFormulation] : []),
      base_formulation: opts.baseFormulation ?? null,
      query: opts.query ?? "",
      n: opts.n ?? 3,
    }),
  doe: (req: Requirement, design: string, engine = "auto") =>
    post<DOEPlan>(`/api/doe?design=${encodeURIComponent(design)}&engine=${encodeURIComponent(engine)}`, req),
  listDoeHistory: (opts: { campaignId?: number | null; page?: number; pageSize?: number } = {}) =>
    get<{ items: Record<string, unknown>[]; total: number; page: number; page_size: number }>(
      `/api/doe/history?page=${opts.page ?? 1}&page_size=${opts.pageSize ?? 20}` +
        (opts.campaignId != null ? `&campaign_id=${opts.campaignId}` : "")
    ),
  startDoeCycle: (req: Requirement, opts: { workbench_campaign_id?: number | null } = {}) =>
    postAccepted("/api/doe/cycle", {
      requirement: req,
      workbench_campaign_id: opts.workbench_campaign_id ?? null,
    }),
  suggestFactors: (req: Requirement) =>
    post<{ factors: FactorCandidate[]; count: number }>("/api/doe/suggest-factors", req),
  kbSources: (
    projectId?: string | null,
    limit = 100,
    opts?: { includeGlobal?: boolean; includeArchived?: boolean },
  ) => {
    const q = new URLSearchParams();
    q.set("limit", String(limit));
    if (projectId) q.set("project_id", projectId);
    if (opts?.includeGlobal) q.set("include_global", "true");
    if (opts?.includeArchived) q.set("include_archived", "true");
    return get<KBSourcesResponse>(`/api/kb/sources?${q}`);
  },

  archiveKbSource: (sourceId: string, archived = true) =>
    post<{ ok: boolean; source_id: string; archived: boolean }>(
      `/api/kb/sources/${encodeURIComponent(sourceId)}/archive`,
      { archived },
    ),

  deleteKbSource: (sourceId: string) =>
    del<{
      ok: boolean;
      source_id: string;
      chunks_removed: number;
      mentions_removed: number;
      links_removed: number;
      wiki_pages_touched: number;
    }>(`/api/kb/sources/${encodeURIComponent(sourceId)}`),
  activeDoe: (
    req: Requirement,
    opts: {
      n_suggest?: number;
      doe_design?: string;
      engine?: string;
      doe_engine?: string;
      campaign_state?: string | null;
      workbench_campaign_id?: number | null;
      existing_records?: ExperimentRecord[];
      budget_remaining?: number | null;
    } = {}
  ) =>
    post<ActiveDoeResult>("/api/doe/active", {
      ...req,
      existing_records: opts.existing_records,
      n_suggest: opts.n_suggest ?? 4,
      doe_design: opts.doe_design ?? "lhs",
      engine: opts.engine ?? "auto",
      doe_engine: opts.doe_engine ?? "auto",
      campaign_state: opts.campaign_state ?? null,
      workbench_campaign_id: opts.workbench_campaign_id ?? null,
      budget_remaining: opts.budget_remaining ?? null,
    }),
  // ── Inverse design ──
  startInverseDesign: (
    req: Requirement,
    targets: TargetSpec,
    opts: { population?: number; generations?: number; seed_with_llm?: boolean } = {}
  ) =>
    postAccepted("/api/design/inverse", {
      requirement: req,
      targets,
      population: opts.population ?? 48,
      generations: opts.generations ?? 30,
      seed_with_llm: opts.seed_with_llm ?? true,
    }),

  // ── Material substitution ──
  findSubstitutes: (body: {
    requirement?: Requirement;
    formulation?: Formulation;
    material?: string;
    slot_index?: number;
    limit?: number;
    include_unavailable?: boolean;
    include_external?: boolean;
    external_limit?: number;
    similarity_threshold?: number;
    include_literature?: boolean;
    literature_limit?: number;
    include_surechembl?: boolean;
    surechembl_limit?: number;
    include_llm?: boolean | null;
    llm_limit?: number;
  }) => post<SubstitutionReport>("/api/materials/substitutes", body),

  supplyRisk: () => get<SupplyRiskReport>("/api/materials/supply-risk"),

  setMaterialAvailability: (name: string, availability: string) =>
    post<unknown>("/api/materials/availability", { name, availability }),

  // ── Experiments and QC reports ──
  listExperiments: (opts: { domain?: string; project_id?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.domain) params.set("domain", opts.domain);
    if (opts.project_id) params.set("project_id", opts.project_id);
    params.set("limit", String(opts.limit ?? 100));
    return get<ExperimentSummary[]>(`/api/experiments?${params}`);
  },

  /** Cross-campaign keyword search (tags/notes/params). */
  searchExperiments: (q: string) => {
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    return get<ExperimentSearchHit[]>(`/api/experiments/search?${qs}`);
  },

  uploadQcReport: async (
    file: File,
    target: number | { experiment_id?: number; campaign_id?: number; row_id?: number },
    opts: { project_id?: string; sync_measured?: boolean } = {}
  ): Promise<QCReportResult> => {
    const body = new FormData();
    body.append("file", file);
    if (typeof target === "number") {
      body.append("experiment_id", String(target));
    } else {
      if (target.experiment_id != null)
        body.append("experiment_id", String(target.experiment_id));
      if (target.campaign_id != null)
        body.append("campaign_id", String(target.campaign_id));
      if (target.row_id != null)
        body.append("row_id", String(target.row_id));
    }
    if (opts.project_id) body.append("project_id", opts.project_id);
    body.append("sync_measured", String(opts.sync_measured ?? true));
    const res = await fetch("/api/qc/report", {
      method: "POST",
      headers: apiAuthHeaders(),
      body,
    });
    if (!res.ok) throw await readApiError(res, "/api/qc/report");
    return res.json();
  },

  listWorkbenchCampaigns: () =>
    get<WorkbenchCampaignSummary[]>(`/api/experiments/workbench/campaigns`),

  experimentMeasurements: (experimentId: number) =>
    get<{
      experiment_id: number;
      measurements: QCMeasurementView[];
      attachments: { id: string; source_document_id: string; kind: string }[];
    }>(`/api/qc/experiments/${experimentId}/measurements`),

  getWorkbenchRowMeasurements: (campaignId: number, rowId: number) =>
    get<{
      experiment_id: number;
      measurements: QCMeasurementView[];
      attachments: { id: string; source_document_id: string; kind: string }[];
    }>(`/api/qc/workbench/${campaignId}/rows/${rowId}/measurements`),

  uploadAttachment: async (
    file: File,
    experimentId: number,
    opts: { kind?: string; note?: string } = {}
  ): Promise<Attachment> => {
    const body = new FormData();
    body.append("file", file);
    if (opts.kind) body.append("kind", opts.kind);
    if (opts.note) body.append("note", opts.note);
    const res = await fetch(`/api/experiments/${experimentId}/attachments`, {
      method: "POST",
      headers: apiAuthHeaders(),
      body,
    });
    if (!res.ok)
      throw await readApiError(res, "/api/experiments/attachments");
    return res.json();
  },

  getAttachments: (experimentId: number) =>
    get<Attachment[]>(`/api/experiments/${experimentId}/attachments`),

  getWorkbenchAttachments: (campaignId: number, rowId: number) =>
    get<Attachment[]>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments`
    ),

  workbenchAttachmentDownloadUrl: (
    campaignId: number,
    rowId: number,
    attachmentId: string
  ) =>
    `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments/${attachmentId}/download`,

  getWorkbenchVersions: (campaignId: number, rowId: number) =>
    get<{
      refcode: string;
      versions: {
        id: string;
        version: number;
        action?: string;
        timestamp?: string;
        creator?: string | null;
      }[];
    }>(`/api/experiments/workbench/${campaignId}/rows/${rowId}/versions`),

  compareWorkbenchVersions: (
    campaignId: number,
    rowId: number,
    v1: string,
    v2: string
  ) =>
    get<{ refcode: string; diff: Record<string, unknown> }>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/versions?compare_v1=${v1}&compare_v2=${v2}`
    ),

  restoreWorkbenchVersion: (
    campaignId: number,
    rowId: number,
    versionId: string
  ) =>
    post<{ restored: boolean; refcode?: string }>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/versions/${versionId}/restore`,
      {}
    ),

  deleteWorkbenchAttachment: (
    campaignId: number,
    rowId: number,
    attachmentId: string
  ) =>
    del<{ deleted: boolean }>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments/${attachmentId}`
    ),

  uploadWorkbenchAttachment: async (
    file: File,
    campaignId: number,
    rowId: number,
    opts: { kind?: string; note?: string } = {}
  ): Promise<Attachment> => {
    const body = new FormData();
    body.append("file", file);
    if (opts.kind) body.append("kind", opts.kind);
    if (opts.note) body.append("note", opts.note);
    const res = await fetch(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/attachments`,
      { method: "POST", headers: apiAuthHeaders(), body }
    );
    if (!res.ok)
      throw await readApiError(res, "/api/experiments/workbench/attachments");
    return res.json();
  },

  // ── Formulation revision history ──
  saveFormulationVersion: (body: {
    formulation: Formulation;
    lineage_id?: string | null;
    parent_version_id?: string | null;
    change_summary?: string;
    created_by?: string;
  }) => post<FormulationVersionView>("/api/formulations/versions", body),

  findFormulationLineages: (name: string, domain: string, limit = 10) => {
    const params = new URLSearchParams({ name, domain, limit: String(limit) });
    return get<{ lineage_id: string; versions: FormulationVersionView[] }[]>(
      `/api/formulations/versions?${params}`
    );
  },

  formulationLineage: (lineageId: string) =>
    get<{ lineage_id: string; versions: FormulationVersionView[] }>(
      `/api/formulations/versions/${encodeURIComponent(lineageId)}`
    ),

  diffFormulationVersions: (fromId: string, toId: string) =>
    get<VersionDiffResult>(
      `/api/formulations/versions/${encodeURIComponent(fromId)}/diff/${encodeURIComponent(toId)}`
    ),

  startOptimize: (
    req: Requirement,
    iterations: number,
    engine = "auto",
    campaignState?: string | null,
    workbenchCampaignId?: number | null
  ) =>
    postAccepted("/api/optimize", {
      requirement: req,
      iterations,
      engine,
      campaign_state: campaignState ?? null,
      workbench_campaign_id: workbenchCampaignId ?? null,
    }),

  submitDeepResearch: (
    topic: string,
    req: Requirement,
    sources: Evidence[],
    query = ""
  ) =>
    postAccepted("/api/research/deep", { topic, requirement: req, sources, query }),

  submitRecommendResearch: (
    req: Requirement,
    sources: Evidence[] = [],
    query = "",
    opts: { preferMaterialsCatalog?: boolean } = {}
  ) =>
    postAccepted("/api/research/recommend", {
      ...req,
      sources,
      query,
      prefer_materials_catalog: Boolean(opts.preferMaterialsCatalog),
    }),

  task: async (id: string): Promise<TaskStatus> => {
    const res = await fetch(`/api/tasks/${id}`, { headers: apiAuthHeaders() });
    if (!res.ok) throw new Error(`task ${id} -> ${res.status}`);
    return res.json();
  },

  cancelTask: async (id: string): Promise<TaskStatus> => {
    const res = await fetch(`/api/tasks/${id}/cancel`, { method: "POST", headers: apiAuthHeaders() });
    if (!res.ok) throw new Error(`cancel ${id} -> ${res.status}`);
    return res.json();
  },
  submitExperiments: (records: ExperimentRecord[]) =>
    post<TrainingReport>("/api/experiments", { records, retrain: true }),
  createWorkbenchCampaign: (
    plan: DOEPlan,
    name?: string,
    strategy?: string,
    requirement?: Requirement,
    projectId?: string
  ) =>
    post<WorkbenchCampaignResponse>("/api/experiments/workbench/campaigns", {
      plan,
      name,
      strategy,
      requirement,
      project_id: projectId,
    }),
  getWorkbenchCampaign: (campaignId: number) =>
    get<WorkbenchCampaignResponse>(`/api/experiments/workbench/${campaignId}`),
  getWorkbenchQuality: (campaignId: number) =>
    get<WorkbenchQuality>(`/api/experiments/workbench/${campaignId}/quality`),
  reconcileWorkbench: (campaignId: number) =>
    post<ReconcileResult>(`/api/experiments/workbench/${campaignId}/reconcile`, {}),
  listCampaignRounds: (campaignId: number, opts: { page?: number; pageSize?: number } = {}) =>
    get<{ rounds: Record<string, unknown>[]; total_rounds: number; page: number; page_size: number; unassociated_ledger: number }>(
      `/api/experiments/workbench/${campaignId}/rounds?page=${opts.page ?? 1}&page_size=${opts.pageSize ?? 5}`
    ),

  getRowLineage: (campaignId: number, rowId: number) =>
    get<WorkbenchRow[]>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/lineage`
    ),
  /** Field-only tags write (PUT .../tags) — avoids full-row sync races (A9). */
  updateWorkbenchRowTags: (campaignId: number, rowId: number, tags: string[]) =>
    put<WorkbenchRow>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/tags`,
      { tags }
    ),
  /** Field-only note write (PUT .../note) — avoids full-row sync races (A9). */
  updateWorkbenchRowNote: (campaignId: number, rowId: number, note: string | null) =>
    put<WorkbenchRow>(
      `/api/experiments/workbench/${campaignId}/rows/${rowId}/note`,
      { note }
    ),
  syncWorkbench: (body: BatchUpdateRequest) =>
    put<WorkbenchSyncResponse>("/api/experiments/workbench/sync", body),
  models: () => get<ModelInfo[]>("/api/models"),
  modelVersions: (projectId: string, metric: string) =>
    get<ModelVersionMeta[]>(
      `/api/models/versions?project_id=${encodeURIComponent(projectId)}&metric=${encodeURIComponent(metric)}`
    ),
  rollbackModel: (projectId: string, metric: string, versionId: string) =>
    post<ModelInfo>("/api/models/rollback", {
      project_id: projectId,
      metric,
      version_id: versionId,
    }),
  trainingStatus: () => get<TrainingStatus>("/api/training-status"),
  doeExportUrl: (planId: string, format: "csv" | "xlsx" = "csv") =>
    `/api/doe/${planId}/export?format=${format}`,
  importExperimentsCsv: async (file: File, domain?: ProductDomain): Promise<TrainingReport> => {
    const fd = new FormData();
    fd.append("file", file);
    const q = domain ? `?domain=${domain}` : "";
    const res = await fetch(`/api/experiments/import-csv${q}`, {
      method: "POST",
      headers: apiAuthHeaders(),
      body: fd,
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        // ignore parse failure, keep status code
      }
      throw new Error(detail);
    }
    return res.json();
  },

  search: (req: SearchRequest) =>
    post<SearchResponse>("/api/search", req),

  searchStream: (req: SearchRequest) => postAccepted("/api/search/stream", req),

  notebooklmStatus: () =>
    get<NotebookLMStatus>("/api/notebooklm/auth-status"),

  notebooklmConfig: (cfg: { enabled?: boolean; notebook_id?: string }) =>
    post<NotebookLMStatus>("/api/notebooklm/config", cfg),

  notebooklmLogin: () =>
    post<NotebookLMLoginResult>("/api/notebooklm/login", {}),

  // Uploads are queued (202) and parsed by a Celery worker: OCR on a scan runs
  // for minutes and holding the request open is what made a proxy in front of
  // uvicorn 502 a job that had actually succeeded. The SSE stream carries the
  // final evidence back, so callers still get one resolved value.
  ingest: async (file: File): Promise<IngestResponse> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/ingest", { method: "POST", headers: apiAuthHeaders(), body: fd });
    if (res.status !== 202) throw await readApiError(res, "/api/ingest");
    const accepted = (await res.json()) as { task_id: string };
    const final = await awaitTaskStream(accepted.task_id, undefined, 0, undefined, 900_000);
    return (final.data ?? {}) as unknown as IngestResponse;
  },

  ingestBatch: async (files: File[]): Promise<IngestResponse & { files_processed?: number }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    const res = await fetch("/api/ingest/batch", {
      method: "POST",
      headers: apiAuthHeaders(),
      body: fd,
    });
    if (res.status !== 202) throw await readApiError(res, "/api/ingest/batch");
    const accepted = (await res.json()) as { task_id: string };
    const final = await awaitTaskStream(accepted.task_id, undefined, 0, undefined, 900_000);
    return (final.data ?? {}) as unknown as IngestResponse & { files_processed?: number };
  },

  ingestUrl: (url: string) =>
    post<IngestResponse>("/api/ingest/url", { url }),

  ingestText: (text: string, title?: string) =>
    post<IngestResponse>("/api/ingest/text", { text, title: title ?? "Pasted text" }),

  // ── 2026-09-05 统一摄取入口(DOI/arXiv/专利号/URL → 全文 → 入库, OA 破墙) ──
  ingestTask: (docType: string, identifier: string) =>
    post<IngestResponse>("/api/ingest/task", { doc_type: docType, identifier }),

  // ── 材料库管理(GET /api/materials 已挂载但隐藏于 OpenAPI schema) ──
  listMaterials: (params?: {
    q?: string;
    role?: string;
    availability?: string;
    functional_class?: string;
    substitute_group?: string;
    include_archived?: boolean;
  }) => {
    const q = new URLSearchParams();
    if (params?.q) q.set("q", params.q);
    if (params?.role) q.set("role", params.role);
    if (params?.availability) q.set("availability", params.availability);
    if (params?.functional_class) q.set("functional_class", params.functional_class);
    if (params?.substitute_group) q.set("substitute_group", params.substitute_group);
    if (params?.include_archived) q.set("include_archived", "true");
    const qs = q.toString();
    return get<MaterialListResponse>(`/api/materials${qs ? `?${qs}` : ""}`);
  },

  upsertMaterial: (spec: MaterialSpec) =>
    post<MaterialView>("/api/materials", spec),

  importMaterials: async (file: File, dryRun = true) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/materials/import?dry_run=${dryRun ? "true" : "false"}`, {
      method: "POST",
      headers: apiAuthHeaders(),
      body: fd,
    });
    if (!res.ok) throw new Error(`/api/materials/import -> ${res.status}`);
    return res.json() as Promise<MaterialImportPreview>;
  },

  exportMaterialsUrl: (format: "json" | "csv" | "xlsx", params?: { q?: string; role?: string }) => {
    const q = new URLSearchParams({ format });
    if (params?.q) q.set("q", params.q);
    if (params?.role) q.set("role", params.role);
    return `/api/materials/export?${q}`;
  },

  materialsImportTemplateUrl: (format: "json" | "csv" | "xlsx" = "csv") =>
    `/api/materials/import-template?format=${format}`,

  listMaterialCandidates: (limit = 200) =>
    get<{ total: number; candidates: MaterialCandidate[] }>(`/api/materials/candidates?limit=${limit}`),

  promoteMaterialCandidate: (id: string) =>
    post<{ ok: boolean; action?: string; name?: string }>(`/api/materials/candidates/${id}/promote`, {}),

  dismissMaterialCandidate: (id: string) =>
    post<{ ok: boolean }>(`/api/materials/candidates/${id}/dismiss`, {}),

  dismissNoisyMaterialCandidates: (source = "kb_promoted") =>
    post<{ dismissed: number; kept: number; scanned: number }>(
      `/api/materials/candidates/dismiss-noise?source=${encodeURIComponent(source)}`,
      {}
    ),

  proposeMaterial: (body: {
    name: string;
    role?: string;
    cas_no?: string;
    smiles?: string;
    source?: string;
    source_ref?: string;
  }) => post<{ action: string; name: string; reason?: string; origin?: string }>(
    "/api/materials/propose",
    body
  ),

  proposeMaterialsMany: (
    materials: Array<{ name: string; role?: string; cas_no?: string; smiles?: string }>,
    source = "formula"
  ) => post<Record<string, number>>("/api/materials/propose-many", { materials, source }),

  harvestKbProducts: (limit = 200, minMentions = 2) =>
    post<Record<string, number>>(
      `/api/materials/harvest-kb-products?limit=${limit}&min_mentions=${minMentions}`,
      {}
    ),

  archiveMaterial: (name: string, archived = true) =>
    post<MaterialView>("/api/materials/archive", { name, archived }),

  promoteFromRequirement: (requirement: Requirement) =>
    post<{
      upserted?: number;
      pending?: number;
      skipped?: number;
      [k: string]: unknown;
    }>("/api/materials/promote-from-requirement", { requirement }),

  enrichMaterials: () =>
    post<{ enriched: number }>("/api/chemical/enrich-materials", {}),

  // ── 化学结构搜索(SMARTS 子结构 / Murcko 骨架替代) ──
  substructureSearch: (smarts: string, topK = 20) => {
    const q = new URLSearchParams({ smarts, top_k: String(topK) });
    return get<ChemicalHit[]>(`/api/chemical/substructure?${q}`);
  },

  scaffoldSubstitutes: (smiles: string, topK = 20) => {
    const q = new URLSearchParams({ smiles, top_k: String(topK) });
    return get<ChemicalHit[]>(`/api/chemical/scaffold-substitutes?${q}`);
  },

  // ── 会话记忆(多会话聊天; 2026-09-05: 入库项目数据库, 支持项目过滤) ──
  listSessions: (limit = 20, projectId?: string) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (projectId) q.set("project_id", projectId);
    return get<SessionListResponse>(`/api/session/list?${q}`);
  },

  saveSession: (body: {
    session_id: string;
    history: unknown[];
    context?: Record<string, unknown>;
    ttl_seconds?: number;
    project_id?: string;
    title?: string;
  }) => post<{ ok: boolean }>("/api/session/save", body),

  loadSession: (sessionId: string) =>
    get<SessionLoadResponse>(`/api/session/load/${sessionId}`),

  sessionInfo: (sessionId: string) =>
    get<SessionInfoResponse>(`/api/session/info/${sessionId}`),

  deleteSession: (sessionId: string) =>
    del<{ ok: boolean }>(`/api/session/delete/${sessionId}`),

  // ── KB 诊断: 切块详情 / 完整性 ──
  kbChunksBySource: (sourceId: string) =>
    get<KbChunk[]>(`/api/kb/chunks/by-source/${sourceId}`),

  kbIntegrity: () =>
    get<KbIntegrityResponse>("/api/kb/integrity"),

  /** Power-user chunk probe (GET /api/kb/search). */
  kbSearch: (q: string, k = 6, projectId?: string) => {
    const qs = new URLSearchParams({ q, k: String(k) });
    if (projectId) qs.set("project_id", projectId);
    return get<{ results: Evidence[] }>(`/api/kb/search?${qs}`);
  },

  /** BM25 + vector hybrid probe (POST /api/kb/hybrid-search). */
  kbHybridSearch: (query: string, topK = 10, alpha = 0.3) =>
    post<KbSearchChunk[]>("/api/kb/hybrid-search", {
      query,
      top_k: topK,
      alpha,
    }),

  /** Scored KB retrieval probe (POST /api/kb/query-test). */
  kbQueryTest: (body: KbQueryTestRequest) =>
    post<KbQueryTestResponse>("/api/kb/query-test", body),

  /** Shared probe ↔ recommend retrieval knobs (GET /api/kb/retrieval-settings). */
  kbRetrievalSettings: () => get<KbRetrievalSettings>("/api/kb/retrieval-settings"),

  /** Curated golden retrieval questions (GET /api/kb/golden-questions). */
  kbGoldenQuestions: () => get<KbGoldenQuestion[]>("/api/kb/golden-questions"),

  /** Run golden eval batch (POST /api/kb/golden-eval/run). */
  kbGoldenEvalRun: (body: KbGoldenEvalRequest) =>
    post<KbGoldenEvalResponse>("/api/kb/golden-eval/run", body),

  // ── KG 维护: 统计 / 重建 / 挂源 ──
  kgStats: () => get<KgStats>("/api/kg/stats"),

  /** Hub materials KG canvas (SQLite links; Neo4j not required). */
  kgGraph: (params?: { relation_types?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.relation_types) q.set("relation_types", params.relation_types);
    if (params?.limit != null) q.set("limit", String(params.limit));
    const qs = q.toString();
    return get<KgMaterialGraphResponse>(`/api/kg/graph${qs ? `?${qs}` : ""}`);
  },

  /** KG ranking weight calibration snapshot (GET /api/kg/calibration). */
  kgCalibration: () =>
    get<KgCalibrationResponse>("/api/kg/calibration"),

  /** Shortest relation path between two entity ids (GET /api/kg/path). */
  kgPath: (src: string, dst: string, maxDepth = 4) => {
    const qs = new URLSearchParams({
      src,
      dst,
      max_depth: String(maxDepth),
    });
    return get<KgPathResponse>(`/api/kg/path?${qs}`);
  },

  /** KG-aware evidence retrieve probe (POST /api/kg/retrieve). */
  kgRetrieve: (
    query: string,
    opts: { mode?: string; projectId?: string; kSemantic?: number } = {}
  ) =>
    post<KgRetrieveResponse>("/api/kg/retrieve", {
      query,
      mode: opts.mode ?? "auto",
      project_id: opts.projectId ?? null,
      k_semantic: opts.kSemantic ?? 6,
    }),

  /** Commercial product registry from corpus (GET /api/kb/products). */
  kbProducts: (q = "", limit = 50, offset = 0) => {
    const qs = new URLSearchParams({
      q,
      limit: String(limit),
      offset: String(offset),
    });
    return get<KbProductsResponse>(`/api/kb/products?${qs}`);
  },

  /** P3.2 — one-click fulltext ingest from an Evidence row. */
  ingestEvidence: (body: {
    identifier: string;
    title?: string | null;
    url?: string | null;
    url_alt?: string | null;
    source?: string | null;
    project_id?: string | null;
    assignee?: string | null;
    pub_date?: string | null;
    snippet?: string | null;
    oa_pdf_url?: string | null;
    is_oa?: boolean | null;
    relevance?: number;
  }) =>
    post<{
      ok: boolean;
      status: string;
      source_id?: string | null;
      task_id?: string | null;
      status_url?: string | null;
      canonical_id?: string | null;
      reason?: string | null;
      kind?: string | null;
    }>("/api/kb/ingest-evidence", body),

  kgRebuild: () =>
    post<KgRebuildReport>("/api/kg/rebuild", {}),

  kgLinkSource: (sourceId: string) =>
    post<KgLinkReport>(`/api/kg/link-source/${sourceId}`, {}),

  /** SureChEMBL P3: upsert patent + chemistry entities (appears_in/claimed_in). */
  surechemblIngestDocument: (body: {
    doc_id: string;
    title?: string | null;
    assignee?: string | null;
    pub_date?: string | null;
    url?: string | null;
    section?: string | null;
    fetch_chemistry?: boolean;
    chemistry_limit?: number;
  }) =>
    post<{
      ok: boolean;
      doc_id: string;
      patent_entity_id: string;
      entities: number;
      links: number;
      link_type: string;
      chemicals: number;
    }>("/api/surechembl/kg/ingest-document", body),

  /** SureChEMBL P3: extract review-only embodiment Formulation draft (no DB writes). */
  surechemblExtractExampleDraft: (body: {
    doc_id: string;
    title?: string | null;
    assignee?: string | null;
    pub_date?: string | null;
    url?: string | null;
    domain?: string;
    ingredient_limit?: number;
  }) =>
    post<{
      ok: boolean;
      draft: SurechemblExampleDraft;
      reason?: string;
      chemistry_count?: number;
    }>("/api/surechembl/extract-example-draft", body),

  /** SureChEMBL P3: human confirm → KG + pending materials; never production pool. */
  surechemblConfirmExampleDraft: (draft: SurechemblExampleDraft) =>
    post<{
      ok: boolean;
      doc_id: string;
      pending_materials: { action: string; name?: string; reason?: string }[];
      formulation_entity_id: string;
      promoted_to_pool: boolean;
      note?: string;
    }>("/api/surechembl/confirm-example-draft", { draft }),

  /** P3.1: batch eligibility for ingested-fulltext embodiment extract. */
  embodimentEligibility: (sourceIds: string[]) =>
    post<{ items: EmbodimentEligibilityItem[] }>(
      "/api/formulations/embodiment-eligibility",
      { source_ids: sourceIds }
    ),

  /** P3.1: extract review-only draft from KB SourceDocument (no live fetch). */
  extractEmbodimentDraft: (body: {
    source_id: string;
    domain?: string;
    surechembl_hint?: boolean;
  }) =>
    post<{
      ok: boolean;
      draft: EmbodimentDraft;
      reason?: string;
      eligibility?: EmbodimentEligibilityItem;
    }>("/api/formulations/extract-embodiment-draft", body),

  /** P3.1: human confirm → KG + pending; never production pool. */
  confirmEmbodimentDraft: (draft: EmbodimentDraft) =>
    post<{
      ok: boolean;
      source_id?: string;
      doc_id?: string;
      pending_materials: { action: string; name?: string; reason?: string }[];
      formulation_entity_id: string;
      promoted_to_pool: boolean;
      amount_source?: string;
      note?: string;
    }>("/api/formulations/confirm-embodiment-draft", { draft }),

  kgRelationsRebuild: (sourceId?: string, opts?: { limit?: number }) =>
    post<{ task_id: string; status_url: string }>("/api/kg/relations/rebuild", {
      source_id: sourceId ?? null,
      limit: opts?.limit ?? 50,
    }),

  // ── Neo4j 图谱适配层 ──
  neo4jStats: () =>
    get<Neo4jStats>("/api/kg/neo4j/stats"),

  neo4jCompounds: (q = "", limit = 50) => {
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    qs.set("limit", String(limit));
    return get<Neo4jCompound[]>(`/api/kg/neo4j/compounds?${qs}`);
  },

  neo4jFormulations: (limit = 50) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    return get<Neo4jFormulation[]>(`/api/kg/neo4j/formulations?${qs}`);
  },

  neo4jEnsureSchema: () =>
    post<{ ok: boolean }>("/api/kg/neo4j/schema/ensure", {}),

  neo4jUpsertCompound: (spec: Record<string, unknown>) =>
    post<Record<string, unknown>>("/api/kg/neo4j/compounds", spec),

  neo4jCompoundSimilar: (compUid: string) =>
    get<Neo4jHit[]>(`/api/kg/neo4j/compounds/${compUid}/similar`),

  neo4jUpsertFormulation: (spec: Record<string, unknown>) =>
    post<Record<string, unknown>>("/api/kg/neo4j/formulations", spec),

  neo4jFormulationCompounds: (formUid: string) =>
    get<Neo4jHit[]>(`/api/kg/neo4j/formulations/${formUid}/compounds`),

  /** Link formulation CONTAINS compound (POST /api/kg/neo4j/formulations/{form}/compounds/{comp}). */
  neo4jLinkContains: (formUid: string, compUid: string, ratio?: number) => {
    const qs = new URLSearchParams();
    if (ratio != null) qs.set("ratio", String(ratio));
    const suffix = qs.toString() ? `?${qs}` : "";
    return post<Neo4jLinkResponse>(
      `/api/kg/neo4j/formulations/${encodeURIComponent(formUid)}/compounds/${encodeURIComponent(compUid)}${suffix}`,
      {}
    );
  },

  /** Link two formulations as similar (POST /api/kg/neo4j/formulations/{a}/similar/{b}). */
  neo4jLinkSimilar: (formA: string, formB: string, score = 1.0) => {
    const qs = new URLSearchParams({ score: String(score) });
    return post<Neo4jLinkResponse>(
      `/api/kg/neo4j/formulations/${encodeURIComponent(formA)}/similar/${encodeURIComponent(formB)}?${qs}`,
      {}
    );
  },

  listProjects: () => get<import("../projectWorkspace").ProjectSummary[]>("/api/projects"),

  listFormulationSkills: () => get<FormulationSkill[]>("/api/formulation-skills"),

  getFormulationSkill: (id: string) =>
    get<FormulationSkill>(`/api/formulation-skills/${encodeURIComponent(id)}`),

  getMeta: () =>
    get<{
      domains: string[];
      substrates: string[];
      designs: string[];
      example_projects: { id: string; label: string; domain?: string }[];
      builtin_metrics?: string[];
      role_catalog?: string[];
      /** Import probes for optional engines (baybe / pydoe / …). */
      engines?: Record<string, { available: boolean; label: string }>;
    }>("/api/meta"),

  getDefaultLevers: (params: {
    domain: ProductDomain;
    substrate?: string;
    cure_temperature_c?: number | null;
  }) => {
    const q = new URLSearchParams();
    q.set("domain", params.domain);
    if (params.substrate) q.set("substrate", params.substrate);
    if (params.cure_temperature_c != null) {
      q.set("cure_temperature_c", String(params.cure_temperature_c));
    }
    return get<{ levers: LeverSpec[] }>(`/api/meta/default-levers?${q.toString()}`);
  },

  createProject: (title = "", requirement?: Requirement) =>
    post<ProjectDetailResponse>("/api/projects", { title, requirement }),

  getProject: (id: string) => get<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`),

  updateProject: (id: string, workspace: import("../projectWorkspace").ProjectWorkspacePayload, title?: string) =>
    put<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`, { workspace, title }),

  deleteProject: (id: string, knowledge: "delete" | "global" = "delete") =>
    del<{ ok: boolean }>(
      `/api/projects/${encodeURIComponent(id)}?knowledge=${knowledge}`
    ),

  getProjectDbStats: (id: string) =>
    get<{
      project_id: string;
      document_count: number;
      campaign_count: number;
      experiment_count: number;
    }>(`/api/projects/${encodeURIComponent(id)}/db-stats`),

  listProjectExports: (id: string) =>
    get<ProjectExportFile[]>(`/api/projects/${encodeURIComponent(id)}/exports`),

  saveProjectExport: (id: string, filename: string, content: string) =>
    post<ProjectExportFile>(`/api/projects/${encodeURIComponent(id)}/exports`, {
      filename,
      content,
    }),

  /** Multipart binary shelf upload (PDF / XLSX / etc.). */
  uploadProjectExport: async (id: string, file: File, filename?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (filename) fd.append("filename", filename);
    const res = await fetch(
      `/api/projects/${encodeURIComponent(id)}/exports/upload`,
      { method: "POST", headers: apiAuthHeaders(), body: fd },
    );
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const j = await res.json();
        detail = j.detail ? JSON.stringify(j.detail) : detail;
      } catch {
        /* ignore */
      }
      throw new Error(`uploadProjectExport failed: ${detail}`);
    }
    return (await res.json()) as ProjectExportFile;
  },

  downloadProjectExportUrl: (id: string, filename: string) =>
    `/api/projects/${encodeURIComponent(id)}/exports/${encodeURIComponent(filename)}`,

  deleteProjectExport: (id: string, filename: string) =>
    del<{ ok: boolean; filename: string }>(
      `/api/projects/${encodeURIComponent(id)}/exports/${encodeURIComponent(filename)}`
    ),

  getProjectHistory: (id: string, limit = 20) =>
    get<ProjectPayloadHistoryResponse>(
      `/api/projects/${encodeURIComponent(id)}/history?limit=${limit}`
    ),

  rollbackProject: (id: string, version: number) =>
    post<ProjectDetailResponse>(
      `/api/projects/${encodeURIComponent(id)}/rollback/${version}`,
      {}
    ),

  migrateLocalProjects: (snapshots: {
    id: string;
    timestamp: string;
    domain: string;
    headline: string;
    requirement: Requirement;
    leaderboard: Formulation[];
    models: ModelInfo[];
    optimization_history: number[];
  }[]) =>
    post<import("../projectWorkspace").ProjectSummary[]>("/api/projects/migrate-local", { snapshots }),

  chat: (req: ChatRequest) => post<ChatResponse>("/api/chat", req),

  /** SSE 流式问答: 逐事件回调; AbortSignal 可中断(组件卸载/停止)。 */
  chatStream: async (
    req: ChatRequest,
    onEvent: (ev: ChatStreamEvent) => void,
    opts: { signal?: AbortSignal } = {},
  ): Promise<void> => {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(req),
      signal: opts.signal,
    });
    if (!res.ok || !res.body) {
      throw await readApiError(res, "/api/chat/stream");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = block
          .split("\n")
          .find((l) => l.startsWith("data: "));
        if (!line) continue; // 心跳注释行等
        try {
          onEvent(JSON.parse(line.slice(6)) as ChatStreamEvent);
        } catch {
          // 单条事件解析失败不中断整个流
        }
      }
    }
  },

  /** 上传结构图 → MolScribe 识别 → SMILES + MolJSON + 相似材料命中。 */
  uploadStructure: async (
    image: File,
    opts: { threshold?: number; top_k?: number } = {}
  ): Promise<StructureRecognitionResult> => {
    const body = new FormData();
    body.append("image", image);
    if (opts.threshold != null) body.append("threshold", String(opts.threshold));
    if (opts.top_k != null) body.append("top_k", String(opts.top_k));
    const res = await fetch("/api/chemical/structure", {
      method: "POST",
      headers: apiAuthHeaders(),
      body,
    });
    if (!res.ok) throw await readApiError(res, "/api/chemical/structure");
    return res.json();
  },

  kbStats: () => get<KBStats>("/api/kb/stats"),

  kbQualityOps: (projectId?: string) =>
    get<KBQualityOps>(
      projectId
        ? `/api/kb/quality-ops?project_id=${encodeURIComponent(projectId)}`
        : "/api/kb/quality-ops",
    ),

  kbReindex: () => post<KBReindexResult>("/api/kb/reindex", {}),

  /** W4: dry-run by default; physical delete needs confirm=true and dry_run=false. */
  kbRetentionPurge: (body: {
    days: number;
    confirm?: boolean;
    dry_run?: boolean;
    limit?: number;
  }) =>
    post<RetentionPurgeResult>("/api/kb/retention/purge", {
      days: body.days,
      confirm: body.confirm ?? false,
      dry_run: body.dry_run ?? true,
      limit: body.limit ?? 200,
    }),

  kgResolve: (q: string) =>
    get<KGEntityResolveResponse>(`/api/kg/resolve?q=${encodeURIComponent(q)}`),

  kgRelations: (entityId: string, direction = "both", limit = 20, extraction_method?: string) => {
    const params = new URLSearchParams({ direction, limit: String(limit) });
    if (extraction_method) params.set("extraction_method", extraction_method);
    return get<KGRelationView[]>(
      `/api/kg/relations/${encodeURIComponent(entityId)}?${params}`
    );
  },

  kgFeedbackStats: () =>
    get<{
      measured_total: number;
      measured_performance: number;
      measured_material?: number;
      measured_domain?: number;
      by_campaign: Record<string, number>;
    }>("/api/kg/feedback/stats"),

  kgFeedbackReport: () =>
    get<{ measured_total: number; measured_performance: number; by_campaign: Record<string, number>; alert: string | null; recent_bias: unknown[] }>(
      "/api/kg/feedback/report"
    ),

  getBiasTrend: (campaignId: number, thresholdRmse = 50) =>
    get<{ campaign_id: number; trend: { at: string | null; n_rows: number; by_metric: Record<string, { n: number; mean_error: number; rmse: number; mae: number; max_abs: number }> }[]; alerts: string[]; threshold_rmse: number }>(
      `/api/experiments/workbench/${campaignId}/bias-trend?threshold_rmse=${thresholdRmse}`
    ),

  kgSubstitutes: (opts: { entityId?: string; q?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (opts.entityId) params.set("entity_id", opts.entityId);
    if (opts.q) params.set("q", opts.q);
    if (opts.limit) params.set("limit", String(opts.limit));
    return get<KGSubstituteDiscoverResponse>(`/api/kg/discover/substitutes?${params}`);
  },

  kgContradictions: (opts: { entityId?: string; q?: string }) => {
    const params = new URLSearchParams();
    if (opts.entityId) params.set("entity_id", opts.entityId);
    if (opts.q) params.set("q", opts.q);
    return get<KGContradictionResponse>(`/api/kg/contradictions?${params}`);
  },

  kgSimilarFormulations: (factors: Record<string, number>, limit = 10) =>
    post<SimilarFormulationResponse>("/api/kg/formulations/similar", { factors, limit }),

  orgDashboard: () => get<OrgDashboardStats>("/api/org/dashboard"),

  listWikiPages: (params?: {
    kind?: string;
    limit?: number;
    offset?: number;
    project_id?: string | null;
  }) => {
    const q = new URLSearchParams();
    if (params?.kind) q.set("kind", params.kind);
    if (params?.limit != null) q.set("limit", String(params.limit));
    if (params?.offset != null) q.set("offset", String(params.offset));
    if (params?.project_id) q.set("project_id", params.project_id);
    const qs = q.toString();
    return get<WikiPagesResponse>(`/api/wiki/pages${qs ? `?${qs}` : ""}`);
  },
  searchWikiPages: (params: {
    q: string;
    kind?: string;
    limit?: number;
    project_id?: string | null;
  }) => {
    const q = new URLSearchParams();
    q.set("q", params.q);
    if (params.kind) q.set("kind", params.kind);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.project_id) q.set("project_id", params.project_id);
    return get<WikiSearchResponse>(`/api/wiki/search?${q}`);
  },
  compileWikiTheme: (body: { system_key?: string; topic?: string; use_llm?: boolean }) =>
    post<{
      ok: boolean;
      path?: string;
      title?: string;
      error?: string;
      llm_generated?: boolean;
      source_ids?: string[];
    }>("/api/wiki/themes/compile", body),
  ensureWikiDossier: (body: {
    project_id: string;
    campaign_id?: string;
    vertical?: string;
    use_llm?: boolean;
  }) =>
    post<{
      ok: boolean;
      path?: string;
      data_path?: string;
      project_id?: string;
      section_revisions?: Record<string, number>;
      error?: string;
    }>("/api/wiki/dossier/ensure", body),
  patchWikiDossier: (body: {
    project_id: string;
    sections?: string[];
    campaign_id?: string;
    vertical?: string;
    use_llm?: boolean;
  }) =>
    post<{
      ok: boolean;
      path?: string;
      patched_sections?: string[];
      skipped_unchanged?: string[];
      section_revisions?: Record<string, number>;
      error?: string;
    }>("/api/wiki/dossier/patch", body),
  refreshWikiDossier: (body: {
    project_id: string;
    sections?: string[];
    campaign_id?: string;
    vertical?: string;
    use_llm?: boolean;
  }) =>
    post<{
      ok: boolean;
      path?: string;
      patched_sections?: string[];
      section_revisions?: Record<string, number>;
      error?: string;
    }>("/api/wiki/dossier/refresh", body),
  getWikiDossier: (projectId: string) =>
    get<{
      page_id?: string;
      path: string;
      data_path?: string;
      title?: string;
      flags?: string[];
      revision?: number;
      markdown: string;
      data?: {
        section_revisions?: Record<string, number>;
        flags?: Record<string, boolean>;
        project_id?: string;
        template?: string;
        [key: string]: unknown;
      } | null;
    }>(`/api/wiki/dossier/${encodeURIComponent(projectId)}`),
  getWikiDossierPack: (projectId: string, campaignId?: string) => {
    const q = campaignId ? `?campaign_id=${encodeURIComponent(campaignId)}` : "";
    return get<Record<string, unknown>>(
      `/api/wiki/dossier/${encodeURIComponent(projectId)}/pack${q}`,
    );
  },
  listWikiReportTemplates: () =>
    get<{
      templates: { id: string; title: string; blurb: string; slices: string }[];
      export?: {
        md?: boolean;
        docx?: boolean;
        pdf?: boolean;
        pptx?: boolean;
        cjk_font?: string | null;
      };
    }>("/api/wiki/reports/templates"),
  generateWikiReport: (body: {
    project_id: string;
    template: string;
    campaign_id?: string;
    prompt?: string;
    use_llm?: boolean;
    ensure_dossier?: boolean;
    persist?: boolean;
  }) =>
    post<{
      ok: boolean;
      project_id?: string;
      template?: string;
      path?: string;
      title?: string;
      markdown?: string;
      page_id?: string;
      revision?: number;
      disclaimer?: string;
      llm?: { used_llm?: boolean; error?: string | null };
      error?: string;
    }>("/api/wiki/dossier/report", body),
  startWikiStormReport: (body: {
    project_id: string;
    topic?: string;
    max_sections?: number;
    perspectives?: string[];
    use_llm?: boolean;
    parallel?: boolean | null;
    max_workers?: number;
    ensure_dossier?: boolean;
    persist?: boolean;
    campaign_id?: string;
  }) =>
    postAccepted("/api/wiki/storm/report", body).then((accepted) => ({
      ...accepted,
      disclaimer: "draft_not_claims" as const,
    })),
  getWikiStormReport: (projectId: string) =>
    get<{
      path: string;
      title: string;
      markdown: string;
      flags?: string[];
      source_ids?: string[];
      disclaimer?: string;
      updated_at?: string | null;
    }>(`/api/wiki/storm/report/${encodeURIComponent(projectId)}`),
  exportWikiStormReport: async (body: {
    project_id: string;
    format: "md" | "docx" | "pdf" | "pptx";
    regenerate?: boolean;
    topic?: string;
    use_llm?: boolean;
    parallel?: boolean | null;
    ensure_dossier?: boolean;
  }) => {
    const res = await fetch("/api/wiki/storm/report/export", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...apiAuthHeaders() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `storm export failed (${res.status})`);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename=\"?([^\";]+)\"?/i.exec(cd);
    return { blob, filename: m?.[1] || `storm.${body.format}` };
  },
  exportWikiReport: async (body: {
    project_id: string;
    template: string;
    format: "md" | "docx" | "pdf" | "pptx";
    campaign_id?: string;
    prompt?: string;
    use_llm?: boolean;
    ensure_dossier?: boolean;
  }) => {
    const res = await fetch("/api/wiki/dossier/report/export", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...apiAuthHeaders() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `export failed (${res.status})`);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename=\"?([^\";]+)\"?/i.exec(cd);
    return { blob, filename: m?.[1] || `report.${body.format}` };
  },
  rebuildWikiFts: () => post<{ ok: boolean; indexed?: number }>("/api/wiki/fts/rebuild", {}),
  /** S2: deterministic wiki catalog from wiki_pages (App-maintained; not SSOT). */
  getWikiCatalog: (params?: {
    limit?: number;
    kinds?: string;
    project_id?: string | null;
    format?: "json";
  }) => {
    const q = new URLSearchParams();
    if (params?.limit != null) q.set("limit", String(params.limit));
    if (params?.kinds) q.set("kinds", params.kinds);
    if (params?.project_id) q.set("project_id", params.project_id);
    q.set("format", "json");
    const qs = q.toString();
    return get<{
      ok: boolean;
      path?: string;
      generated_at?: string;
      entry_count?: number;
      entries?: Array<{
        path: string;
        kind: string;
        title: string;
        norm_key?: string;
        flags?: string[];
        source_ids?: string[];
      }>;
      markdown?: string;
      persisted?: boolean;
    }>(`/api/wiki/catalog${qs ? `?${qs}` : ""}`);
  },
  rebuildWikiCatalog: (body?: {
    persist?: boolean;
    limit?: number;
    kinds?: string;
    project_id?: string | null;
  }) =>
    post<{
      ok: boolean;
      path?: string;
      generated_at?: string;
      entry_count?: number;
      markdown?: string;
      persisted?: boolean;
      disk_path?: string;
    }>("/api/wiki/catalog/rebuild", body ?? { persist: true }),

  /** S4: save Chat/Deep Research answer as L2 queries/ draft (flag-gated). */
  saveWikiDraft: (body: {
    project_id: string;
    question?: string;
    answer_markdown: string;
    title?: string;
    source_ids?: string[];
    citations?: Array<Record<string, unknown>>;
    origin?: "chat" | "deep_research" | string;
  }) =>
    post<{
      ok: boolean;
      path?: string;
      title?: string;
      kind?: string;
      flags?: string[];
      project_id?: string;
      disclaimer?: string;
      updated_at?: string;
    }>("/api/wiki/drafts/save", body),

  downloadWikiCatalogMd: async (params?: {
    limit?: number;
    kinds?: string;
    project_id?: string | null;
  }) => {
    const q = new URLSearchParams();
    q.set("format", "md");
    if (params?.limit != null) q.set("limit", String(params.limit));
    if (params?.kinds) q.set("kinds", params.kinds);
    if (params?.project_id) q.set("project_id", params.project_id);
    const res = await fetch(`/api/wiki/catalog?${q.toString()}`, {
      headers: { ...apiAuthHeaders() },
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `catalog download failed (${res.status})`);
    }
    const blob = await res.blob();
    return { blob, filename: "catalog.md" };
  },
  rebuildWikiEmbed: () =>
    post<{ ok: boolean; indexed?: number; embedded_vectors?: number; reason?: string }>(
      "/api/wiki/embed/rebuild",
      {},
    ),
  reviewWikiPage: (body: {
    path: string;
    reviewed?: boolean;
    human_override?: string;
  }) =>
    post<{
      ok: boolean;
      path?: string;
      reviewed?: boolean | null;
      human_override?: string | null;
      flags?: string[];
    }>("/api/wiki/pages/review", body),
  getWikiPage: (id: string) => get<WikiPageDetail>(`/api/wiki/pages/${encodeURIComponent(id)}`),
  getWikiByPath: (path: string) =>
    get<WikiPageDetail>(`/api/wiki/by-path?path=${encodeURIComponent(path)}`),
  listWikiFlags: (params?: { limit?: number; project_id?: string | null }) => {
    const q = new URLSearchParams();
    if (params?.limit != null) q.set("limit", String(params.limit));
    if (params?.project_id) q.set("project_id", params.project_id);
    const qs = q.toString();
    return get<WikiFlagsResponse>(`/api/wiki/flags${qs ? `?${qs}` : ""}`);
  },
  runWikiLint: (body?: { limit?: number; detect_orphan?: boolean }) =>
    post<{
      ok: boolean;
      scanned?: number;
      flagged?: number;
      orphan_count?: number;
      broken_count?: number;
      results?: Record<string, string[]>;
    }>("/api/wiki/lint/run", body ?? {}),

  /** S1: re-lint flagged pages and clear obsolete lint flags. */
  sweepWikiLint: (body?: { limit?: number; detect_orphan?: boolean }) =>
    post<{
      ok: boolean;
      scanned?: number;
      cleared?: number;
      still_flagged?: number;
      orphan_count?: number;
      results?: Record<string, string[]>;
    }>("/api/wiki/lint/sweep", body ?? {}),

  /** S5: explicit click — rewrite broken wikilink or append ## Related (no L1 Claims). */
  applyWikiBrokenFix: (body: {
    path: string;
    broken: string;
    replacement_path: string;
    mode?: string;
  }) =>
    post<{
      ok: boolean;
      path?: string;
      broken?: string;
      replacement_path?: string;
      wikilink?: string;
      mode?: string;
      flags?: string[];
    }>("/api/wiki/lint/apply-broken", body),

  /** Wiki page [[wikilink]] graph (P0 canvas). Requires wiki_page_graph_enabled. */
  getWikiPageGraph: (params?: {
    limit?: number;
    kinds?: string;
    include_orphan?: boolean;
    project_id?: string | null;
  }) => {
    const q = new URLSearchParams();
    if (params?.limit != null) q.set("limit", String(params.limit));
    if (params?.kinds) q.set("kinds", params.kinds);
    if (params?.include_orphan === false) q.set("include_orphan", "false");
    if (params?.include_orphan === true) q.set("include_orphan", "true");
    if (params?.project_id) q.set("project_id", params.project_id);
    const qs = q.toString();
    return get<WikiPageGraphResponse>(`/api/wiki/graph${qs ? `?${qs}` : ""}`);
  },

  getEnvFlags: () => get<{ flags: EnvFlag[] }>("/api/settings/env-flags"),

  postEnvFlags: (updates: Record<string, boolean>) =>
    post<{ updated: string[]; rejected: string[]; flags: EnvFlag[] }>(
      "/api/settings/env-flags",
      { updates },
    ),

  getFormulationMode: () =>
    get<{ current: string; choices: { value: string; label: string; desc: string }[] }>(
      "/api/settings/formulation-mode",
    ),

  getParseProfile: () =>
    get<{
      profile: string
      profiles: string[]
      availability: {
        gpu_available: boolean
        mineru_key_present: boolean
        vision_available: boolean
        active_rag_backend: string
      }
    }>("/api/settings/parse-profile"),

  postParseProfile: (profile: string) =>
    post<{
      profile: string
      persisted: boolean
      env: Record<string, string>
      availability: { gpu_available: boolean; mineru_key_present: boolean; vision_available: boolean; active_rag_backend: string }
    }>("/api/settings/parse-profile", { profile }),

  setFormulationMode: (mode: string) =>
    post<{ mode: string; status: string }>("/api/settings/formulation-mode", { mode }),

  getWikiChatMode: () =>
    get<{
      current: string;
      choices: { value: string; label: string; desc: string }[];
    }>("/api/settings/wiki-chat-mode"),

  setWikiChatMode: (mode: string) =>
    post<{ mode: string; status: string }>("/api/settings/wiki-chat-mode", { mode }),

  postDoeCyclePause: (campaignId: number | string, isPaused: boolean) =>
    post<{ status: string; message: string }>(
      `/api/experiments/hooks/pause-doecycle/${campaignId}`,
      { isPaused },
    ),

  getDoeCycleStatus: (campaignId: number | string) =>
    get<{
      isPaused: boolean;
      lastUpdated: string | null;
      campaignId: number;
      degraded?: boolean;
    }>(`/api/experiments/hooks/doecyle-status/${campaignId}`),

  getOcsr: () => get<{ status: OcsrStatus }>("/api/settings/ocsr"),

  getSettings: () => get<LLMSettingsResponse>("/api/settings"),

  getAuthStatus: () =>
    get<{ auth_required: boolean; hint: string; multi_user?: boolean; owner?: string }>("/api/auth/status"),

  /** Public liveness probe — no auth required. */
  getHealth: () => get<PlatformHealth>("/health"),

  postSettings: (update: Partial<LLMConfig> & { api_key?: string }) =>
    post<{ ok: boolean; provider: string; model: string; message: string }>(
      "/api/settings",
      {
        provider: update.provider,
        model: update.model,
        api_key: update.api_key,
        base_url: update.baseUrl,
      }
    ),

  postVisionSettings: (update: VisionSettingsUpdate) =>
    post<{ ok: boolean } & VisionSettings>("/api/settings/vision", {
      provider: update.provider,
      model: update.model,
      api_key: update.api_key,
      base_url: update.baseUrl,
    }),

  /** Send a real image down the real path — the only honest capability check. */
  testVision: () => post<VisionProbeResult>("/api/settings/vision/test", {}),

  refreshLlmModels: (opts?: { provider?: string; baseUrl?: string; model?: string }) =>
    post<LlmModelsRefreshResponse>("/api/settings/models/refresh", {
      provider: opts?.provider,
      baseUrl: opts?.baseUrl,
      model: opts?.model,
    }),

  testConnection: () =>
    post<{ ok: boolean; provider: string; model: string; message: string }>(
      "/api/settings/test", {}
    ),

  getSecrets: () => get<SecretsListResponse>("/api/settings/secrets"),

  postSecrets: (updates: Record<string, string>) =>
    post<SecretsListResponse>("/api/settings/secrets", { updates }),

  testSecret: (id: string) =>
    post<{ ok: boolean; message: string }>("/api/settings/secrets/test", { id }),

  analyzeIP: (req: IPAnalysisRequest) =>
    post<IPReport>("/api/ip/analyze", req),

  loopIterate: (
    req: Requirement,
    optimize_iterations = 24,
    n_suggest = 4,
    optimize_engine = "auto",
    doe_engine = "auto",
    opts: {
      workbench_campaign_id?: number | null;
      campaign_state?: string | null;
      prior_rmse_history?: Record<string, number>[];
      prior_optimization?: OptimizationResult | null;
      prior_next_doe?: DOEPlan | null;
      budget_remaining?: number | null;
    } = {}
  ) =>
    postAccepted("/api/loop/iterate", {
      ...req,
      optimize_iterations,
      n_suggest,
      optimize_engine,
      doe_engine,
      workbench_campaign_id: opts.workbench_campaign_id ?? null,
      campaign_state: opts.campaign_state ?? null,
      prior_rmse_history: opts.prior_rmse_history ?? [],
      prior_optimization: opts.prior_optimization ?? null,
      prior_next_doe: opts.prior_next_doe ?? null,
      budget_remaining: opts.budget_remaining ?? null,
    }),

  parseIntent: (text: string) =>
    post<IntentResult>("/api/intent/parse", { text }),

  loadExampleProject: (exampleId: string) =>
    get<Requirement>(`/api/examples/${encodeURIComponent(exampleId)}`),

  getSourceStatus: () =>
    get<Record<string, SourceStatus>>("/api/search/status"),

  getRagStatus: () =>
    get<{ backend: string; formulation_mode: string; gpu_enabled: boolean; gpu_available: boolean; rag_backend_setting: string; prewarm: { status: string; backend: string | null; elapsed_ms: number | null; error: string | null } }>("/api/research/rag/status"),

  prewarmRag: (background = true) =>
    post<{ status: string; backend: string | null; elapsed_ms: number | null; error: string | null }>(`/api/research/rag/prewarm?background=${String(background)}`, {}),

  listDependencies: () =>
    get<DependencyListResponse>("/api/dependencies"),

  installDependencies: (names: string[], upgrade = false) =>
    postAccepted("/api/dependencies/install", { names, upgrade }),
};

