/** Stamp active Hub project onto a requirement when project_id is empty. */

export type WithProjectId = { project_id?: string | null };

export function withActiveProjectId<T extends WithProjectId>(
  requirement: T,
  activeProjectId: string | null | undefined,
): T {
  const existing = (requirement.project_id ?? "").trim();
  if (existing) return requirement;
  const pid = (activeProjectId ?? "").trim();
  if (!pid) return requirement;
  return { ...requirement, project_id: pid };
}
