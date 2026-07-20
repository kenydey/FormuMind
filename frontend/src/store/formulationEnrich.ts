import { api, type Formulation, type Requirement } from "../api";
import type { SliceGet, SliceSet } from "./sliceTypes";

/** Server-side catalog + lookup enrich for leaderboard formulations. */
export async function enrichFormulationsViaValidate(
  forms: Formulation[],
  requirement?: Requirement | null
): Promise<{ formulations: Formulation[]; warnings: string[] }> {
  if (!forms.length) {
    return { formulations: [], warnings: [] };
  }
  try {
    const res = await api.validateFormulations(forms, requirement);
    return {
      formulations: res.formulations ?? forms,
      warnings: res.warnings ?? [],
    };
  } catch {
    return { formulations: forms, warnings: [] };
  }
}

/** Replace leaderboard with validate-enriched forms and store API warnings. */
export async function applyEnrichedLeaderboard(
  set: SliceSet,
  get: SliceGet,
  forms: Formulation[],
  extra?: (draft: import("./types").AppState) => void
): Promise<Formulation[]> {
  const { requirement } = get();
  const { formulations, warnings } = await enrichFormulationsViaValidate(forms, requirement);
  set((draft) => {
    draft.leaderboard = formulations;
    draft.formulationValidateWarnings = warnings;
    extra?.(draft);
  });
  get().scheduleAutosave();
  return formulations;
}
