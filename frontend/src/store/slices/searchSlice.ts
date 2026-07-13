import {
  api,
  awaitTaskStream,
  formatApiError,
  parseKbIngestData,
  parseSearchStreamData,
  sanitizeEvidenceForApi,
} from "../../api";
import type { Evidence, SourceStatus } from "../../api";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

export function createSearchSlice(set: SliceSet, get: SliceGet) {
  return {
    setSearchQuery: (q) => {
      set((draft) => {
        draft.searchQuery = q;
      });
      get().scheduleAutosave();
    },

    setSourceTypes: (types) => {
      set((draft) => {
        draft.sourceTypes = types;
      });
      get().scheduleAutosave();
    },

    setRecommendSourceTypes: (types) => {
      set((draft) => {
        draft.recommendSourceTypes = types;
      });
      get().scheduleAutosave();
    },

    addSources: (evidence) => {
      set((draft) => {
        const fresh = evidence.filter(
          (e) =>
            !draft.sources.some(
              (x) => (x.identifier || x.title) === (e.identifier || e.title)
            )
        );
        const freshIds = fresh.map((e) => e.identifier || e.title);
        draft.sources.push(...fresh);
        for (const id of freshIds) {
          if (!draft.selectedSources.includes(id)) {
            draft.selectedSources.push(id);
          }
        }
      });
      get().scheduleAutosave();
    },

    removeSource: (id) => {
      set((draft) => {
        draft.sources = draft.sources.filter((e) => (e.identifier || e.title) !== id);
        draft.selectedSources = draft.selectedSources.filter((x) => x !== id);
      });
      get().scheduleAutosave();
    },

    clearSources: () => {
      set((draft) => {
        draft.sources = [];
        draft.selectedSources = [];
        draft.chatHistory = [];
      });
      get().scheduleAutosave();
    },

    toggleSourceSelected: (id) => {
      set((draft) => {
        if (draft.selectedSources.includes(id)) {
          draft.selectedSources = draft.selectedSources.filter((x) => x !== id);
        } else {
          draft.selectedSources.push(id);
        }
      });
      get().scheduleAutosave();
    },

    selectAllSources: () =>
      set((draft) => {
        draft.selectedSources = draft.sources.map((e) => e.identifier || e.title);
      }),

    deselectAllSources: () =>
      set((draft) => {
        draft.selectedSources = [];
      }),

    searchSources: async (queryOverride?: string) => {
      const { searchQuery, requirement, sourceTypes } = get();
      const query = (queryOverride ?? searchQuery).trim();
      if (queryOverride !== undefined) {
        set((draft) => {
          draft.searchQuery = query;
        });
      }
      set((draft) => {
        draft.searchBusy = true;
        draft.error = null;
        draft.sources = [];
        draft.selectedSources = [];
        draft.usedSeedFallback = false;
        draft.searchProgress = {
          message: "正在排队…",
          total: 0,
          source: null,
          newCount: 0,
          sourcesDone: [],
          sourcesPending: [],
        };
      });
      const types = sourceTypes.filter((t) => t !== "local");
      try {
        const { task_id } = await api.searchStream({
          query,
          requirement,
          source_types: types.length ? types : undefined,
          total_limit: 300,
        });
        const final = await awaitTaskStream(
          task_id,
          (ev) => {
            const { evidence, progress, usedSeedFallback } = parseSearchStreamData(
              ev.data as Record<string, unknown> | undefined
            );
            set((draft) => {
              if (usedSeedFallback) draft.usedSeedFallback = true;
              draft.searchProgress = {
                message: ev.message || draft.searchProgress?.message || "检索中…",
                total: progress.total ?? draft.searchProgress?.total ?? 0,
                source: progress.source ?? null,
                newCount: progress.newCount ?? 0,
                sourcesDone: progress.sourcesDone ?? [],
                sourcesPending: progress.sourcesPending ?? [],
              };
            });
            if (evidence.length) get().addSources(evidence);
          },
          300_000
        );
        const r = final.data as
          | {
              evidence?: Evidence[];
              source_status?: Record<string, SourceStatus>;
              used_seed_fallback?: boolean;
              kb_ingest_task_id?: string;
            }
          | undefined;
        if (r?.evidence?.length) get().addSources(r.evidence);
        // Background KB build runs server-side; track it without blocking —
        // the search UI is already done at this point.
        if (r?.kb_ingest_task_id) void get().trackKbIngest(r.kb_ingest_task_id);
        if (r?.source_status) {
          set((draft) => {
            draft.sourceStatus = r.source_status!;
          });
        }
        if (r?.used_seed_fallback || r?.evidence?.some((e) => e.is_seed_corpus)) {
          set((draft) => {
            draft.usedSeedFallback = true;
          });
        }
        set((draft) => {
          draft.searchProgress = draft.searchProgress
            ? {
                ...draft.searchProgress,
                message: final.message || `检索完成，共 ${draft.sources.length} 条`,
              }
            : null;
        });
        get().scheduleAutosave();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.searchBusy = false;
          draft.searchProgress = null;
        });
      }
    },

    trackKbIngest: async (taskId) => {
      set((draft) => {
        draft.kbIngest = {
          taskId,
          docs: [],
          done: 0,
          total: 0,
          indexed: 0,
          failed: 0,
          message: "知识库后台构建中…",
          active: true,
        };
      });
      try {
        const final = await awaitTaskStream(
          taskId,
          (ev) => {
            const progress = parseKbIngestData(ev.data as Record<string, unknown> | undefined);
            set((draft) => {
              if (!draft.kbIngest || draft.kbIngest.taskId !== taskId) return;
              if (progress) {
                draft.kbIngest.docs = progress.docs;
                draft.kbIngest.done = progress.done;
                draft.kbIngest.total = progress.total;
                draft.kbIngest.indexed = progress.indexed;
                draft.kbIngest.failed = progress.failed;
              }
              if (ev.message) draft.kbIngest.message = ev.message;
            });
          },
          600_000
        );
        const progress = parseKbIngestData(final.data as Record<string, unknown> | undefined);
        set((draft) => {
          if (!draft.kbIngest || draft.kbIngest.taskId !== taskId) return;
          if (progress) {
            draft.kbIngest.docs = progress.docs;
            draft.kbIngest.done = progress.done;
            draft.kbIngest.total = progress.total;
            draft.kbIngest.indexed = progress.indexed;
            draft.kbIngest.failed = progress.failed;
          }
          draft.kbIngest.message = final.message || "知识库构建完成";
          draft.kbIngest.active = false;
        });
      } catch (e) {
        set((draft) => {
          if (!draft.kbIngest || draft.kbIngest.taskId !== taskId) return;
          draft.kbIngest.message = `知识库构建中断：${formatApiError(e)}`;
          draft.kbIngest.active = false;
        });
      }
    },

    dismissKbIngest: () => {
      set((draft) => {
        draft.kbIngest = null;
      });
    },

    loadSourceStatus: async () => {
      try {
        const status = await api.getSourceStatus();
        set((draft) => {
          draft.sourceStatus = status;
        });
      } catch {
        // silently ignore
      }
    },

    hydrateLlmSettings: async () => {
      try {
        const remote = await api.getSettings();
        const local = get().llmConfig;
        set((draft) => {
          draft.llmConfig.provider = remote.provider || local.provider;
          draft.llmConfig.model = remote.model || local.model;
          draft.llmConfig.baseUrl = remote.base_url ?? local.baseUrl;
        });
      } catch {
        // offline — keep persisted provider/model
      }
    },

    uploadFiles: async (files) => {
      if (files.length === 0) return;
      set((draft) => {
        draft.searchBusy = true;
        draft.error = null;
      });
      try {
        const res =
          files.length === 1
            ? await api.ingest(files[0])
            : await api.ingestBatch(files);
        get().addSources(res.evidence);
      } catch (e) {
        set((draft) => {
          draft.error = `文件上传失败：${e instanceof Error ? e.message : String(e)}`;
        });
      } finally {
        set((draft) => {
          draft.searchBusy = false;
        });
      }
    },

    sendChat: async (question) => {
      const { sources, selectedSources, requirement } = get();
      const active = sources
        .filter((e) => selectedSources.includes(e.identifier || e.title))
        .map(sanitizeEvidenceForApi);
      set((draft) => {
        draft.chatBusy = true;
        draft.error = null;
        draft.chatHistory.push({ role: "user", content: question });
      });
      try {
        const res = await api.chat({
          question,
          sources: active,
          domain: requirement.domain,
        });
        set((draft) => {
          draft.chatHistory.push({
            role: "assistant",
            content: res.answer,
            citations: res.citations,
            kbChunksUsed: res.kb_chunks_used ?? 0,
          });
          draft.error = null;
        });
        get().scheduleAutosave();
      } catch (e) {
        const msg = formatApiError(e);
        const hint =
          msg.includes("401") || msg.toLowerCase().includes("api token")
            ? " — 请在设置页填写 API 访问令牌，或将 FORMUMIND_API_AUTH_ENABLED=false"
            : "";
        set((draft) => {
          draft.error = `问答失败：${msg}${hint}`;
        });
      } finally {
        set((draft) => {
          draft.chatBusy = false;
        });
      }
    },
  } as Pick<AppState, 'setSearchQuery' | 'setSourceTypes' | 'setRecommendSourceTypes' | 'addSources' | 'removeSource' | 'clearSources' | 'toggleSourceSelected' | 'selectAllSources' | 'deselectAllSources' | 'searchSources' | 'trackKbIngest' | 'dismissKbIngest' | 'loadSourceStatus' | 'hydrateLlmSettings' | 'uploadFiles' | 'sendChat'>;
}
