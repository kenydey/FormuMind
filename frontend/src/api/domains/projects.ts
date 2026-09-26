// Domain facade: projects (P2)
import { apiMethods } from "../methods";

export const projectsApi = {
  deleteProject: apiMethods.deleteProject,
  deleteProjectExport: apiMethods.deleteProjectExport,
  getProject: apiMethods.getProject,
  getProjectDbStats: apiMethods.getProjectDbStats,
  getProjectHistory: apiMethods.getProjectHistory,
  listProjectExports: apiMethods.listProjectExports,
  listProjects: apiMethods.listProjects,
  saveProjectExport: apiMethods.saveProjectExport,
} as const;

export type ProjectsApi = typeof projectsApi;
