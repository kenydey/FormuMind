import {
  api,
  awaitTaskStream,
  BACKEND_UNREACHABLE_MESSAGE,
  formatApiError,
  parseKbIngestData,
  parseSearchStreamData,
  probeBackend,
  sanitizeEvidenceForApi,
} from "../../api";
import type { ChatMessage, Evidence, SourceStatus } from "../../api";
import { undismiss } from "../notifications";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

/**
 * How long silence is allowed before the KB build is presumed dead.
 *
 * Generous on purpose: a single document can spend `fulltext_timeout_s` (20 s)
 * downloading and then minutes in the PDF parser, and with a scanned patent going
 * through OCR at seconds per page a quiet stretch is normal. This only needs to be
 * shorter than "forever".
 */
const KB_INGEST_STALL_MS = 15 * 60 * 1000;

/**
 * How long a federated search may sit silent before being presumed dead.
 *
 * Search is streamed source-by-source; a quiet stretch means one source is
 * taking a while (a slow patent API, a paginated crawl), not that the whole
 * task died. 5 minutes is far beyond any single source's page time while still
 * catching a worker that has actually gone away.
 */
const SEARCH_STALL_MS = 5 * 60 * 1000;

/**
 * Error detail that must not pre-empt the reachability verdict.
 *
 * `formatApiError` rewrites any network-ish string into the generic "后端不可达"
 * copy. That is the right default for a bare request, but wrong inside the
 * stream→sync recovery: there the reachability question is answered by an actual
 * probe, and a pre-substituted verdict would both pre-empt it and make the two
 * branches indistinguishable. So collapse-to-unreachable is undone, and the raw
 * message (e.g. "Failed to fetch", "503: broker unavailable") is shown as detail.
 */
function detailOf(err: unknown): string {
  const formatted = formatApiError(err);
  if (formatted !== BACKEND_UNREACHABLE_MESSAGE) return formatted;
  return err instanceof Error ? err.message : String(err);
}

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

    setNotebooklmNotebookId: (id) => {
      set((draft) => {
        draft.notebooklmNotebookId = id;
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
        draft.filterReport = null;
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

    searchSources: async (queryOverride?: string, opts?: { append?: boolean }) => {
      const { searchQuery, requirement, sourceTypes, notebooklmNotebookId } = get();
      const query = (queryOverride ?? searchQuery).trim();
      // 「添加数据源」走累加（append=true）：保留现有 sources 与研究主题，
      // 新关键词结果经 addSources 去重后追加；左栏「开始检索」保持清空重搜。
      const append = opts?.append === true;
      if (queryOverride !== undefined && !append) {
        set((draft) => {
          draft.searchQuery = query;
        });
      }
      set((draft) => {
        draft.searchBusy = true;
        draft.error = null;
        if (!append) {
          draft.sources = [];
          draft.selectedSources = [];
        }
        draft.usedSeedFallback = false;
        draft.filterReport = null;
        undismiss(draft.notificationsDismissed, [
          "search", "filter-report", "seed-fallback", "deep-report",
        ]);
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
      if (types.includes("notebooklm") && !notebooklmNotebookId.trim()) {
        set((draft) => {
          draft.searchBusy = false;
          draft.error = "请先为本项目配置 NotebookLM Notebook ID";
          draft.searchProgress = null;
        });
        get().setOpenModal("notebooklm-setup");
        return;
      }
      try {
        const { task_id } = await api.searchStream({
          query,
          requirement,
          source_types: types.length ? types : undefined,
          total_limit: 300,
          notebooklm_notebook_id: types.includes("notebooklm")
            ? notebooklmNotebookId.trim() || undefined
            : undefined,
        });
        const final = await awaitTaskStream(
          task_id,
          (ev) => {
            const { evidence, progress, usedSeedFallback, filterReport } = parseSearchStreamData(
              ev.data as Record<string, unknown> | undefined
            );
            set((draft) => {
              if (usedSeedFallback) draft.usedSeedFallback = true;
              if (filterReport) draft.filterReport = filterReport;
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
          // No wall-clock limit. Federated search streams source-by-source and
          // its total duration depends on how many sources are enabled and how
          // slow each is — a fixed 300 s cut a healthy search off mid-crawl, and
          // then the kb_ingest_task_id never reached the client, so the whole
          // background KB build became invisible. The stall clock (reset on
          // every progress event) still catches a dead worker.
          0,
          undefined,
          SEARCH_STALL_MS
        );
        const r = final.data as
          | {
              evidence?: Evidence[];
              source_status?: Record<string, SourceStatus>;
              used_seed_fallback?: boolean;
              filter_report?: import("../../api").FilterReport;
              kb_ingest_task_id?: string;
            }
          | undefined;
        if (r?.evidence?.length) get().addSources(r.evidence);
        if (r?.filter_report) {
          set((draft) => {
            draft.filterReport = r.filter_report!;
          });
        }
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
      } catch (streamErr) {
        // Detail text for the stream error, kept visible while we try sync
        // POST /api/search so NotificationStack (and any left-rail consumer of
        // draft.error) shows the 503/broker detail instead of a silent chat
        // lockout. `formatApiError` already collapses network-ish errors into
        // the generic "unreachable" copy — that verdict is not its call to make
        // here, so in that case keep the raw message and let the probe below
        // decide the headline.
        const streamMsg = detailOf(streamErr);
        // A broken stream is not evidence that the API is down — the task may
        // simply have outlived its client. Probe before labelling, so a dropped
        // SSE or a backend restart window is not reported as an unreachable
        // server the user would go hunting for.
        const backendUp = await probeBackend();
        set((draft) => {
          draft.error = backendUp
            ? `流式检索中断（${streamMsg}），改用同步检索…`
            : `流式检索中断，后端暂不可达，改用同步检索…`;
          draft.searchProgress = {
            message: "流式检索失败，改用同步检索…",
            total: draft.searchProgress?.total ?? 0,
            source: null,
            newCount: 0,
            sourcesDone: draft.searchProgress?.sourcesDone ?? [],
            sourcesPending: [],
          };
        });
        try {
          const r = await api.search({
            query,
            requirement,
            source_types: types.length ? types : undefined,
            total_limit: 300,
            notebooklm_notebook_id: types.includes("notebooklm")
              ? notebooklmNotebookId.trim() || undefined
              : undefined,
          });
          if (r.evidence?.length) get().addSources(r.evidence);
          if (r.filter_report) {
            set((draft) => {
              draft.filterReport = r.filter_report!;
            });
          }
          if (r.source_status) {
            set((draft) => {
              draft.sourceStatus = r.source_status!;
            });
          }
          if (r.used_seed_fallback || r.evidence?.some((e) => e.is_seed_corpus)) {
            set((draft) => {
              draft.usedSeedFallback = true;
            });
          }
          set((draft) => {
            // Clarify recovery so the user sees why stream failed but chat can unlock.
            draft.error = backendUp
              ? `流式检索失败（${streamMsg}），已自动改用同步检索并成功`
              : `流式检索失败（${streamMsg}）；后端已恢复，同步检索成功`;
            draft.searchProgress = {
              message: `同步检索完成，共 ${draft.sources.length} 条`,
              total: r.total ?? draft.sources.length,
              source: null,
              newCount: r.evidence?.length ?? 0,
              sourcesDone: draft.searchProgress?.sourcesDone ?? [],
              sourcesPending: [],
            };
          });
          get().scheduleAutosave();
        } catch (syncErr) {
          const syncMsg = detailOf(syncErr);
          // Second probe: only claim the API is unreachable once it has actually
          // stopped answering. Otherwise this is a task-level failure and saying
          // "unreachable" would send the user after the wrong problem.
          const stillUp = await probeBackend();
          set((draft) => {
            draft.error = stillUp
              ? `流式检索失败（${streamMsg}）；同步检索也失败：${syncMsg}`
              : `${BACKEND_UNREACHABLE_MESSAGE}（流式：${streamMsg}；同步：${syncMsg}）`;
          });
        }
      } finally {
        set((draft) => {
          draft.searchBusy = false;
          draft.searchProgress = null;
        });
      }
    },

    trackKbIngest: async (taskId) => {
      set((draft) => {
        undismiss(draft.notificationsDismissed, ["kb-ingest"]);
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
          // No wall-clock limit. Building a knowledge base from several hundred
          // documents takes as long as the downloads take, and any fixed number
          // is arbitrary — the old 600 s cut a healthy job off at roughly the
          // point it got going. The limit never stopped the server anyway; it
          // only stopped this client watching, so the "中断" it reported was not
          // true.
          0,
          undefined,
          // What makes an unlimited wait safe: the clock resets on every progress
          // event, so a slow job never trips it, but a worker that died stops
          // emitting and gets reported instead of spinning forever.
          KB_INGEST_STALL_MS
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
          // "停止跟踪" rather than "中断": losing the stream does not stop the
          // server-side ingest, and telling the user their build was aborted when
          // it is still running sends them to re-run work already in progress.
          draft.kbIngest.message = `知识库构建：已停止跟踪进度（${formatApiError(e)}）——后台任务可能仍在继续`;
          draft.kbIngest.active = false;
        });
      }
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

    sendChat: async (question, structure) => {
      const { sources, selectedSources, requirement } = get();
      const active = sources
        .filter((e) => selectedSources.includes(e.identifier || e.title))
        .map(sanitizeEvidenceForApi);
      set((draft) => {
        draft.chatBusy = true;
        draft.error = null;
        draft.chatHistory.push({ role: "user", content: question });
        // 占位 assistant 消息 — token 逐段累积。
        draft.chatHistory.push({
          role: "assistant",
          content: "",
          streaming: true,
          phase: "retrieval",
        });
      });
      try {
        const { chatHistory, activeProjectId } = get();
        const reqBody: Parameters<typeof api.chat>[0] = {
          question,
          sources: active,
          domain: requirement.domain,
          project_id: activeProjectId ?? undefined,
          history: chatHistory
            .filter((m) => !m.streaming) // 进行中的占位不算历史
            .slice(-6)
            .map((m) => ({
              role: m.role,
              content: m.content,
              citations: m.citations,
            })),
          structure: structure ?? undefined,
        };
        await api.chatStream(reqBody, (ev) => {
          const last = (d: { chatHistory: ChatMessage[] }) =>
            d.chatHistory[d.chatHistory.length - 1];
          if (ev.type === "phase") {
            set((draft) => {
              const m = last(draft);
              if (m?.role === "assistant" && m.streaming) {
                m.phase = ev.phase;
                if (ev.phase === "answering" || ev.phase === "claims") {
                  m.toolStatus = null;
                }
              }
            });
          } else if (ev.type === "tool_start") {
            set((draft) => {
              const m = last(draft);
              if (m?.role === "assistant" && m.streaming) {
                m.phase = "tools";
                m.toolStatus = ev.label || ev.name;
              }
            });
          } else if (ev.type === "tool_result") {
            set((draft) => {
              const m = last(draft);
              if (m?.role === "assistant" && m.streaming) {
                m.toolStatus = ev.ok ? m.toolStatus : `工具失败: ${ev.name}`;
              }
            });
          } else if (ev.type === "token") {
            set((draft) => {
              const m = last(draft);
              if (m?.role === "assistant" && m.streaming) {
                m.content += ev.delta;
              }
            });
          } else if (ev.type === "done") {
            set((draft) => {
              const m = last(draft);
              if (m?.role === "assistant") {
                m.content = ev.answer;
                m.streaming = false;
                m.phase = undefined;
                m.toolStatus = null;
                m.citations = ev.citations;
                m.kbChunksUsed = ev.kb_chunks_used ?? 0;
              }
              draft.error = null;
            });
            get().scheduleAutosave();
            // 多会话: 每轮完成自动落库(2026-09-05 A1)
            void get().persistChatSession();
          } else if (ev.type === "error") {
            set((draft) => {
              const m = last(draft);
              if (m?.role === "assistant") {
                if (!m.content) m.content = "";
                m.content += `\n\n> ⚠️ ${ev.message}`;
                m.streaming = false;
                m.phase = undefined;
              }
              draft.error = ev.message;
            });
          }
        });
      } catch (e) {
        const msg = formatApiError(e);
        const hint =
          msg.includes("401") || msg.toLowerCase().includes("api token")
            ? " — 请在设置页填写 API 访问令牌，或将 FORMUMIND_API_AUTH_ENABLED=false"
            : "";
        set((draft) => {
          // 移除空的流式占位, 避免留下空白气泡; 有内容的保留(部分生成)。
          const last = draft.chatHistory[draft.chatHistory.length - 1];
          if (last?.role === "assistant" && last.streaming && !last.content) {
            draft.chatHistory.pop();
          } else if (last?.role === "assistant" && last.streaming) {
            last.streaming = false;
            last.phase = undefined;
          }
          draft.error = `问答失败：${msg}${hint}`;
        });
      } finally {
        set((draft) => {
          draft.chatBusy = false;
        });
      }
    },
  } as Pick<AppState, 'setSearchQuery' | 'setSourceTypes' | 'setNotebooklmNotebookId' | 'setRecommendSourceTypes' | 'addSources' | 'removeSource' | 'clearSources' | 'toggleSourceSelected' | 'selectAllSources' | 'deselectAllSources' | 'searchSources' | 'trackKbIngest' | 'loadSourceStatus' | 'hydrateLlmSettings' | 'uploadFiles' | 'sendChat'>;
}
