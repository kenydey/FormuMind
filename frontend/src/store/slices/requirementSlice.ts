import { api, formatApiError } from "../../api";
import { defaultConstraintsForDomain } from "../../constants/constraints";
import { normalizeObjective, normalizeObjectives } from "../../utils/objectiveContract";
import { autosave, objectiveTargetFromRequirement } from "../helpers";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";
import { DOMAIN_OBJECTIVES } from "../types";

export function createRequirementSlice(set: SliceSet, get: SliceGet) {
  return {
    setField: (key, value) => {
      set((d) => {
        d.requirement[key] = value;
      });
      if (key === "substrate" || key === "cure_temperature_c") {
        void get().syncDefaultLevers();
      }
      get().scheduleAutosave();
    },

    setDomain: (d) => {
      set((draft) => {
        draft.requirement.domain = d;
        if (!draft.requirement.objectives.length) {
          draft.requirement.objectives = [...DOMAIN_OBJECTIVES[d]];
        }
      });
      void get().syncDefaultLevers();
      get().scheduleAutosave();
    },

    setLevers: (levers) => {
      set((draft) => {
        draft.requirement.levers = levers;
      });
      get().scheduleAutosave();
    },

    syncDefaultLevers: async () => {
      const { requirement, requirementLocked } = get();
      if (requirementLocked) return;
      try {
        const { levers } = await api.getDefaultLevers({
          domain: requirement.domain,
          substrate: requirement.substrate,
          cure_temperature_c: requirement.cure_temperature_c,
        });
        set((draft) => {
          draft.requirement.levers = levers;
        });
      } catch {
        /* keep existing levers when meta API unavailable */
      }
    },

    loadExampleProject: async (exampleId) => {
      set((draft) => {
        draft.error = null;
      });
      try {
        const req = await api.loadExampleProject(exampleId);
        set((draft) => {
          draft.requirement = req;
          draft.activeConstraints = defaultConstraintsForDomain(req.domain);
          draft.research = null;
          draft.leaderboard = [];
          draft.doePlan = null;
          draft.measured = {};
        });
        get().scheduleAutosave();
      } catch (e) {
        set((draft) => {
          draft.error = formatApiError(e);
        });
      }
    },

    setObjectives: (objectives) => {
      set((draft) => {
        draft.requirement.objectives = objectives;
      });
      get().scheduleAutosave();
    },

    updateObjective: (idx, patch) => {
      set((draft) => {
        const obj = draft.requirement.objectives[idx];
        if (!obj) return;
        const merged = normalizeObjective({ ...obj, ...patch });
        Object.assign(obj, merged);
      });
      get().scheduleAutosave();
    },

    removeObjective: (idx) => {
      set((draft) => {
        draft.requirement.objectives.splice(idx, 1);
      });
      get().scheduleAutosave();
    },

    addObjective: (objective) => {
      set((draft) => {
        draft.requirement.objectives.push(normalizeObjective(objective));
      });
      get().scheduleAutosave();
    },

    resetObjectivesForDomain: (domain) => {
      set((draft) => {
        const req = draft.requirement;
        draft.requirement.objectives = normalizeObjectives(
          DOMAIN_OBJECTIVES[domain].map((o) => ({
            ...o,
            target_value:
              objectiveTargetFromRequirement(req, o.metric) ?? o.target_value ?? null,
          }))
        );
      });
      get().scheduleAutosave();
    },

    setActiveConstraints: (keys) => {
      set((draft) => {
        draft.activeConstraints = keys;
      });
      get().scheduleAutosave();
    },

    setConstraintValue: (key, value) => {
      set((draft) => {
        draft.requirement[key] = value;
      });
      get().scheduleAutosave();
    },

    clearConstraintValue: (key) => {
      set((draft) => {
        if (key === "voc_limit_gpl") draft.requirement.voc_limit_gpl = null;
        else if (key === "cure_temperature_c") draft.requirement.cure_temperature_c = null;
        else if (key === "ph_target") draft.requirement.ph_target = null;
        else draft.requirement[key] = 0;
      });
      get().scheduleAutosave();
    },

    addCustomConstraint: (name, value) => {
      const trimmed = name.trim();
      if (!trimmed) return;
      set((draft) => {
        if (!draft.requirement.constraint_values) draft.requirement.constraint_values = {};
        draft.requirement.constraint_values[trimmed] = value;
      });
      get().scheduleAutosave();
    },

    removeCustomConstraint: (name) => {
      set((draft) => {
        if (draft.requirement.constraint_values) {
          delete draft.requirement.constraint_values[name];
        }
      });
      get().scheduleAutosave();
    },

    updateCustomConstraint: (name, value) => {
      set((draft) => {
        if (!draft.requirement.constraint_values) draft.requirement.constraint_values = {};
        draft.requirement.constraint_values[name] = value;
      });
      get().scheduleAutosave();
    },

    captureRequirementSnapshot: () => {
      set((draft) => {
        draft.requirementSnapshot = JSON.parse(JSON.stringify(draft.requirement));
      });
    },

    resetRequirement: () => {
      const snap = get().requirementSnapshot;
      if (!snap) return;
      set((draft) => {
        draft.requirement = JSON.parse(JSON.stringify(snap));
        draft.requirementLocked = false;
      });
      get().scheduleAutosave();
    },

    saveRequirementAndRefresh: async () => {
      if (autosave.timer) {
        clearTimeout(autosave.timer);
        autosave.timer = null;
      }
      let { activeProjectId, requirement } = get();
      if (!activeProjectId) {
        await get().createProject(requirement.product_type || "新项目");
        activeProjectId = get().activeProjectId;
      }
      try {
        await get().saveProject();
      } catch {
        return;
      }
      get().captureRequirementSnapshot();
      set((draft) => {
        draft.requirementLocked = true;
      });
      await get().runResearch();
    },

    unlockRequirement: () => {
      set((draft) => {
        draft.requirementLocked = false;
      });
    },
  } as Pick<AppState, 'setField' | 'setDomain' | 'setLevers' | 'syncDefaultLevers' | 'loadExampleProject' | 'setObjectives' | 'updateObjective' | 'removeObjective' | 'addObjective' | 'resetObjectivesForDomain' | 'setActiveConstraints' | 'setConstraintValue' | 'clearConstraintValue' | 'addCustomConstraint' | 'removeCustomConstraint' | 'updateCustomConstraint' | 'captureRequirementSnapshot' | 'resetRequirement' | 'saveRequirementAndRefresh' | 'unlockRequirement'>;
}
