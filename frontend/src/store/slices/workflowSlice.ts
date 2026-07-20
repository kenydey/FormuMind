import { api, awaitTaskStream, formatApiError, progressToTaskStatus } from "../../api";
import type { DOEPlan, ExperimentRecord, LoopReport, OptimizationResult } from "../../api";
import { extractMeasuredValues, objectiveMetrics } from "../../utils/objectiveContract";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

function applyLoopReportToDraft(draft: AppState, report: LoopReport): void {
  draft.loopReport = report;
  draft.rmseHistory.push(report.rmse_by_metric);
  draft.models = report.model_info;
  draft.lastAlEngine = report.engine;
  if (report.campaign_state) {
    draft.campaignState = report.campaign_state;
  }
  const skipReplace = report.converged && report.optimization.top_formulations.length === 0;
  if (!skipReplace) {
    draft.leaderboard = report.optimization.top_formulations;
    draft.optimizationHistory = report.optimization.history;
    draft.doePlan = report.next_doe;
    draft.measured = {};
  }
}

export function createWorkflowSlice(set: SliceSet, get: SliceGet) {
  return {
    runOptimize: async () => {
      set((draft) => {
        draft.busy = "optimizing";
        draft.error = null;
      });
      try {
        const { requirement, optimizeEngine, campaignState, workbenchCampaignId } = get();
        const { task_id } = await api.startOptimize(
          requirement,
          24,
          optimizeEngine,
          campaignState,
          workbenchCampaignId
        );
        const final = await awaitTaskStream(task_id, (ev) =>
          set((draft) => {
            draft.task = progressToTaskStatus(task_id, "optimize", ev);
          })
        );
        const opt = final.data as unknown as OptimizationResult | null;
        if (opt?.top_formulations) {
          set((draft) => {
            draft.leaderboard = opt.top_formulations;
            draft.optimizationHistory = opt.history;
          });
          get().scheduleAutosave();
        }
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.busy = "idle";
        });
      }
    },

    runLoop: async () => {
      set((draft) => {
        draft.busy = "looping";
        draft.error = null;
      });
      try {
        const {
          requirement,
          optimizeEngine,
          loopDoeEngine,
          workbenchCampaignId,
          campaignState,
          rmseHistory,
          loopReport,
          doePlan,
        } = get();
        const { task_id } = await api.loopIterate(requirement, 24, 4, optimizeEngine, loopDoeEngine, {
          workbench_campaign_id: workbenchCampaignId,
          campaign_state: campaignState,
          prior_rmse_history: rmseHistory,
          prior_optimization: loopReport?.optimization ?? null,
          prior_next_doe: loopReport?.next_doe ?? doePlan ?? null,
        });
        await get().followLoopTask(task_id);
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.busy = "idle";
        });
      }
    },

    followLoopTask: async (taskId: string) => {
      set((draft) => {
        draft.busy = "looping";
        draft.error = null;
      });
      try {
        const final = await awaitTaskStream(taskId, (ev) =>
          set((draft) => {
            draft.task = progressToTaskStatus(taskId, "loop", ev);
          })
        );
        const report = final.data as unknown as LoopReport | null;
        if (report) {
          set((draft) => {
            applyLoopReportToDraft(draft, report);
          });
          get().scheduleAutosave();
        }
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.busy = "idle";
        });
      }
    },

    runNextRoundDoe: async () => {
      set((draft) => {
        draft.busy = "doe";
        draft.error = null;
      });
      try {
        const {
          requirement,
          alEngine,
          loopDoeEngine,
          campaignState,
          workbenchCampaignId,
          activeProjectId,
        } = get();
        const result = await api.activeDoe(requirement, {
          engine: alEngine,
          doe_engine: loopDoeEngine,
          campaign_state: campaignState,
          workbench_campaign_id: workbenchCampaignId,
        });
        const plan = result.plan;
        const wb = await api.createWorkbenchCampaign(
          plan,
          undefined,
          `BayBE-${alEngine}-next`,
          requirement,
          activeProjectId ?? undefined
        );
        set((draft) => {
          draft.doePlan = plan;
          draft.measured = {};
          draft.campaignState = result.campaign_state ?? draft.campaignState;
          draft.lastAlEngine = result.engine;
          draft.workbenchCampaignId = wb.campaign_id;
          draft.workbenchObjectivesSnapshot = wb.objectives_snapshot ?? null;
        });
        await get().refreshWorkbenchStats();
        get().scheduleAutosave();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.busy = "idle";
        });
      }
    },

    setAutoLoopOnSync: (enabled: boolean) => {
      set((draft) => {
        draft.autoLoopOnSync = enabled;
      });
      get().scheduleAutosave();
    },

    applyIntent: async (text) => {
      set((draft) => {
        draft.intentBusy = true;
        draft.error = null;
      });
      try {
        const result = await api.parseIntent(text);
        set((draft) => {
          const r = result.requirement;
          const req = draft.requirement;
          if (r.project_id !== undefined) req.project_id = r.project_id;
          if (r.product_type !== undefined) req.product_type = r.product_type;
          if (r.application !== undefined) req.application = r.application;
          if (r.domain !== undefined) req.domain = r.domain;
          if (r.substrate !== undefined) req.substrate = r.substrate;
          if (r.salt_spray_hours !== undefined) req.salt_spray_hours = r.salt_spray_hours;
          if (r.film_weight_gsm !== undefined) req.film_weight_gsm = r.film_weight_gsm;
          if (r.cure_temperature_c !== undefined) req.cure_temperature_c = r.cure_temperature_c;
          if (r.cleaning_efficiency !== undefined) {
            req.cleaning_efficiency = r.cleaning_efficiency;
          }
          if (r.voc_limit_gpl !== undefined) req.voc_limit_gpl = r.voc_limit_gpl;
          if (r.ph_target !== undefined) req.ph_target = r.ph_target;
          if (r.notes !== undefined) req.notes = r.notes;
          if (r.materials !== undefined) req.materials = r.materials;
          if (r.constraints) {
            if (!req.constraint_values) req.constraint_values = {};
            for (const [k, v] of Object.entries(r.constraints)) {
              if (v != null) req.constraint_values[k] = v;
            }
          }
          if (r.constraint_values) {
            req.constraint_values = { ...req.constraint_values, ...r.constraint_values };
          }
          if (r.objectives?.length) req.objectives = r.objectives;
          if (r.levers?.length) req.levers = r.levers;
        });
        get().scheduleAutosave();
        return result.extracted_fields;
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
        return [];
      } finally {
        set((draft) => {
          draft.intentBusy = false;
        });
      }
    },

    generateDoe: async (design) => {
      set((draft) => {
        draft.busy = "doe";
        draft.error = null;
      });
      try {
        const { requirement, doeEngine, alEngine, campaignState, workbenchCampaignId } = get();
        let plan: DOEPlan;
        let nextCampaignState = campaignState;
        let nextAlEngine: string | null = get().lastAlEngine;
        if (design === "ai_active") {
          const result = await api.activeDoe(requirement, {
            engine: alEngine,
            doe_engine: doeEngine,
            campaign_state: campaignState,
            workbench_campaign_id: workbenchCampaignId,
          });
          plan = result.plan;
          nextCampaignState = result.campaign_state ?? campaignState;
          nextAlEngine = result.engine;
        } else {
          plan = await api.doe(requirement, design, doeEngine);
        }
        const strategy = design === "ai_active" ? `BayBE-${alEngine}` : `DOE-${design}`;
        const { activeProjectId } = get();
        const wb = await api.createWorkbenchCampaign(
          plan,
          undefined,
          strategy,
          requirement,
          activeProjectId ?? undefined
        );
        set((draft) => {
          draft.doePlan = plan;
          draft.measured = {};
          draft.campaignState = nextCampaignState;
          draft.lastAlEngine = nextAlEngine;
          draft.workbenchCampaignId = wb.campaign_id;
          draft.workbenchObjectivesSnapshot = wb.objectives_snapshot ?? null;
        });
        await get().refreshWorkbenchStats();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.busy = "idle";
        });
        get().scheduleAutosave();
      }
    },

    setDoeEngine: (engine) => {
      set((draft) => {
        draft.doeEngine = engine;
      });
      get().scheduleAutosave();
    },

    setAlEngine: (engine) => {
      set((draft) => {
        draft.alEngine = engine;
      });
      get().scheduleAutosave();
    },

    setOptimizeEngine: (engine) => {
      set((draft) => {
        draft.optimizeEngine = engine;
      });
      get().scheduleAutosave();
    },

    setLoopDoeEngine: (engine) => {
      set((draft) => {
        draft.loopDoeEngine = engine;
      });
      get().scheduleAutosave();
    },

    setMeasured: (runId, value) => {
      set((draft) => {
        draft.measured[runId] = value;
      });
      get().scheduleAutosave();
    },

    refreshWorkbenchStats: async () => {
      const id = get().workbenchCampaignId;
      if (id == null) {
        set((draft) => {
          draft.workbenchStats = null;
        });
        return;
      }
      try {
        const wb = await api.getWorkbenchCampaign(id);
        const completed = wb.rows.filter((r) => r.status === "Completed").length;
        set((draft) => {
          draft.workbenchStats = {
            completed,
            total: wb.rows.length,
            name: wb.name,
            strategy: wb.strategy,
          };
          draft.workbenchObjectivesSnapshot =
            wb.objectives_snapshot ?? draft.workbenchObjectivesSnapshot;
        });
      } catch {
        set((draft) => {
          draft.workbenchStats = null;
        });
      }
    },

    ensureWorkbenchCampaign: async () => {
      const { doePlan, workbenchCampaignId, requirement, activeProjectId } = get();
      if (workbenchCampaignId != null) {
        await get().refreshWorkbenchStats();
        return workbenchCampaignId;
      }
      if (!doePlan) return null;
      try {
        const strategy = doePlan.design === "ai_active" ? "BayBE-restore" : `DOE-${doePlan.design}`;
        const wb = await api.createWorkbenchCampaign(
          doePlan,
          undefined,
          strategy,
          requirement,
          activeProjectId ?? undefined
        );
        set((draft) => {
          draft.workbenchCampaignId = wb.campaign_id;
          draft.workbenchObjectivesSnapshot = wb.objectives_snapshot ?? null;
          draft.workbenchStats = {
            completed: 0,
            total: wb.rows.length,
            name: wb.name,
            strategy: wb.strategy,
          };
        });
        get().scheduleAutosave();
        return wb.campaign_id;
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
        return null;
      }
    },

    submitResults: async () => {
      const { doePlan, measured, requirement, workbenchCampaignId } = get();
      if (!doePlan) return;
      const metrics = objectiveMetrics(requirement);
      let records: ExperimentRecord[] = [];

      if (workbenchCampaignId != null) {
        try {
          const wb = await api.getWorkbenchCampaign(workbenchCampaignId);
          for (const r of wb.rows.filter((row) => row.status === "Completed")) {
            const measuredValues = extractMeasuredValues(r.measurements, metrics);
            if (!measuredValues) continue;
            records.push({
              domain: requirement.domain,
              project_id: requirement.project_id,
              factors: { ...(r.planned_params ?? {}), ...(r.actual_params ?? {}) },
              cure_temperature_c:
                (r.actual_params?.cure_temperature_c ?? r.planned_params?.cure_temperature_c) ?? null,
              measured: measuredValues,
              source: "workbench",
            });
          }
        } catch (e) {
          set((draft) => {
            draft.error = formatApiError(e);
          });
          return;
        }
      }

      if (records.length === 0) {
        const primary = metrics[0];
        records = doePlan.runs
          .filter((r) => measured[r.run_id] !== undefined && !Number.isNaN(measured[r.run_id]))
          .map((r) => ({
            domain: requirement.domain,
            project_id: requirement.project_id,
            factors: r.natural,
            cure_temperature_c: r.natural["cure_temperature_c"] ?? null,
            measured: { [primary]: measured[r.run_id] },
            source: "doe",
          }));
      }

      if (records.length === 0) {
        set((draft) => {
          draft.error = "请先在实验台账中完成至少一行实测值，或为实验填写实测值";
        });
        return;
      }
      set((draft) => {
        draft.busy = "training";
        draft.error = null;
      });
      try {
        const report = await api.submitExperiments(records);
        set((draft) => {
          draft.models = report.trained;
          draft.modelHistory.push(report.trained);
          draft.trainMessage = report.message;
        });
        await get().runResearch();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      } finally {
        set((draft) => {
          draft.busy = "idle";
        });
      }
    },

    refreshModels: async () => {
      try {
        const models = await api.models();
        set((draft) => {
          draft.models = models;
        });
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      }
    },

    exportDoe: (format) => {
      const { doePlan } = get();
      if (!doePlan?.plan_id) {
        set((draft) => {
          draft.error = "请先生成 DOE 计划再导出";
        });
        return;
      }
      window.open(api.doeExportUrl(doePlan.plan_id, format), "_blank");
    },

    importCsv: async (file) => {
      set((draft) => {
        draft.busy = "training";
        draft.error = null;
      });
      try {
        const report = await api.importExperimentsCsv(file, get().requirement.domain);
        set((draft) => {
          draft.models = report.trained;
          draft.modelHistory.push(report.trained);
          draft.trainMessage = report.message;
        });
        await get().runResearch();
      } catch (e) {
        set((draft) => {
          draft.error = `CSV 导入失败：${e instanceof Error ? e.message : String(e)}`;
        });
      } finally {
        set((draft) => {
          draft.busy = "idle";
        });
      }
    },
  } as Pick<AppState, 'runOptimize' | 'runLoop' | 'followLoopTask' | 'runNextRoundDoe' | 'setAutoLoopOnSync' | 'applyIntent' | 'generateDoe' | 'setDoeEngine' | 'setAlEngine' | 'setOptimizeEngine' | 'setLoopDoeEngine' | 'setMeasured' | 'refreshWorkbenchStats' | 'ensureWorkbenchCampaign' | 'submitResults' | 'refreshModels' | 'exportDoe' | 'importCsv'>;
}
