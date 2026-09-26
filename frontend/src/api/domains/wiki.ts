// Domain facade: wiki (P2)
import { apiMethods } from "../methods";

export const wikiApi = {
  compileWikiTheme: apiMethods.compileWikiTheme,
  ensureWikiDossier: apiMethods.ensureWikiDossier,
  exportWikiReport: apiMethods.exportWikiReport,
  exportWikiStormReport: apiMethods.exportWikiStormReport,
  getWikiByPath: apiMethods.getWikiByPath,
  getWikiCatalog: apiMethods.getWikiCatalog,
  getWikiChatMode: apiMethods.getWikiChatMode,
  getWikiDossier: apiMethods.getWikiDossier,
  getWikiDossierPack: apiMethods.getWikiDossierPack,
  getWikiPage: apiMethods.getWikiPage,
  getWikiPageGraph: apiMethods.getWikiPageGraph,
  getWikiStormReport: apiMethods.getWikiStormReport,
  listWikiFlags: apiMethods.listWikiFlags,
  listWikiPages: apiMethods.listWikiPages,
  listWikiReportTemplates: apiMethods.listWikiReportTemplates,
  patchWikiDossier: apiMethods.patchWikiDossier,
  searchWikiPages: apiMethods.searchWikiPages,
} as const;

export type WikiApi = typeof wikiApi;
