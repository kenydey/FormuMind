import { api, awaitTaskStream, formatApiError, progressToTaskStatus } from "../../api";
import type { ChatMessage, ComprehensiveReport, Formulation, ResearchResult } from "../../api";
import { applyEnrichedLeaderboard } from "../formulationEnrich";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

export function createResearchSlice(set: SliceSet, get: SliceGet) {
  return {
    setLeaderboard: (forms) => {
      void applyEnrichedLeaderboard(set, get, forms);
    },

    addManualFormula: async () => {
      const { requirement } = get();
      const blank: Formulation = {
        name: "手动配方",
        domain: requirement.domain,
        ingredients: [
          {
            name: "",
            zh_name: "",
            role: "additive",
            weight_pct: 0,
            cas_no: "",
          },
        ],
        rationale: "手动输入",
        predicted: {},
        predicted_std: {},
        score: null,
        warnings: [],
        source: "manual",
      };
      try {
        const { formulation } = await api.addManualFormulation(blank, requirement);
        set((draft) => {
          draft.leaderboard = [...draft.leaderboard, formulation];
        });
        get().scheduleAutosave();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      }
    },

    updateFormulaIngredient: (formulaIdx, ingIdx, patch) => {
      set((draft) => {
        const form = draft.leaderboard[formulaIdx];
        if (!form?.ingredients[ingIdx]) return;
        Object.assign(form.ingredients[ingIdx], patch);
      });
      get().scheduleAutosave();
    },

    runAiModifyFormula: async (prompt, baseIndex = 0) => {
      const { requirement, sources, selectedSources, searchQuery, leaderboard } = get();
      const selected = sources.filter((e) =>
        selectedSources.includes(e.identifier || e.title)
      );
      const payload = selected.length > 0 ? selected : sources;
      const base = leaderboard[baseIndex];
      set((draft) => {
        draft.formulationBusy = true;
        draft.recommendStage = "retrieve";
        draft.recommendMessage = "AI 修改配方中…";
        draft.error = null;
      });
      try {
        const { task_id } = await api.modifyFormulations(requirement, prompt, {
          sources: payload,
          baseFormulas: leaderboard,
          baseFormulation: base,
          query: searchQuery.trim(),
          n: 3,
        });
        const final = await awaitTaskStream(task_id, (ev) => {
          set((draft) => {
            draft.recommendStage = ev.stage ?? "";
            draft.recommendMessage = ev.message ?? "";
            draft.task = progressToTaskStatus(task_id, "recommend", ev);
          });
        });
        const wrapped = final.data as { research?: ResearchResult } | undefined;
        const research = wrapped?.research;
        if (!research?.recommended?.length) throw new Error("AI 修改未返回配方");
        const scored = research.recommended.map((f) => ({
          ...f,
          source: "ai_modify",
        }));
        await applyEnrichedLeaderboard(set, get, [...leaderboard, ...scored], (draft) => {
          draft.research = {
            ...research,
            recommended: draft.leaderboard,
            chat_markdown: `AI 修改：${prompt}`,
          };
        });
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.formulationBusy = false;
          draft.recommendStage = "";
          draft.recommendMessage = "";
        });
      }
    },

    runResearch: async () => {
      set((draft) => {
        draft.formulationBusy = true;
        draft.recommendStage = "retrieve";
        draft.recommendMessage = "正在检索";
        draft.error = null;
      });
      try {
        const { requirement, sources, selectedSources, searchQuery } = get();
        const selected = sources.filter((e) =>
          selectedSources.includes(e.identifier || e.title)
        );
        const payload = selected.length > 0 ? selected : sources;
        const { task_id } = await api.submitRecommendResearch(
          requirement,
          payload,
          searchQuery.trim()
        );
        const final = await awaitTaskStream(task_id, (ev) => {
          set((draft) => {
            draft.recommendStage = ev.stage ?? "";
            draft.recommendMessage = ev.message ?? "";
            draft.task = progressToTaskStatus(task_id, "recommend", ev);
          });
        });
        const wrapped = final.data as { research?: ResearchResult } | undefined;
        const research = wrapped?.research;
        if (!research) throw new Error("推荐未返回结果");
        await applyEnrichedLeaderboard(set, get, research.recommended, (draft) => {
          draft.research = { ...research, recommended: draft.leaderboard };
        });
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.formulationBusy = false;
          draft.recommendStage = "";
          draft.recommendMessage = "";
        });
      }
    },

    runDeepResearch: async () => {
      const { searchQuery, requirement, sources } = get();
      set((draft) => {
        draft.deepResearchBusy = true;
        draft.deepResearchStage = "retrieve";
        draft.deepResearchMessage = "正在检索";
        draft.error = null;
      });
      try {
        const { task_id } = await api.submitDeepResearch(
          searchQuery,
          requirement,
          sources,
          searchQuery.trim()
        );
        const final = await awaitTaskStream(task_id, (ev) => {
          set((draft) => {
            draft.deepResearchStage = ev.stage ?? "";
            draft.deepResearchMessage = ev.message ?? "";
            draft.task = progressToTaskStatus(task_id, "deep_research", ev);
          });
        });
        const wrapped = final.data as { report?: ComprehensiveReport } | undefined;
        const report = wrapped?.report;
        if (!report) throw new Error("深度研究未返回结果");
        set((draft) => {
          draft.deepReport = report;
        });
        if (report.citations?.length) get().addSources(report.citations);
        if (report.candidates?.length) {
          await applyEnrichedLeaderboard(set, get, report.candidates);
        }
        const msg: ChatMessage = {
          role: "assistant",
          content: report.report_markdown,
          citations: report.citations,
        };
        set((draft) => {
          draft.chatHistory.push(msg);
        });
        get().scheduleAutosave();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.deepResearchBusy = false;
          draft.deepResearchStage = "";
          draft.deepResearchMessage = "";
        });
      }
    },

    refreshKnowledgeBase: async () => {
      const query = get().searchQuery.trim();
      if (!query) {
        set((draft) => {
          draft.error = "请先输入研究主题";
        });
        return;
      }
      set((draft) => {
        draft.searchBusy = true;
        draft.error = null;
      });
      try {
        const res = await api.refreshKnowledgeBase(query);
        set((draft) => {
          draft.deepResearchMessage = `已入库 ${res.fetched} 条（索引共 ${res.indexed_total}）`;
        });
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.searchBusy = false;
        });
      }
    },
  } as Pick<AppState, 'setLeaderboard' | 'addManualFormula' | 'updateFormulaIngredient' | 'runAiModifyFormula' | 'runResearch' | 'runDeepResearch' | 'refreshKnowledgeBase'>;
}
