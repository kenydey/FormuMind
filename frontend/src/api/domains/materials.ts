// Domain facade: materials (P2)
import { apiMethods } from "../methods";

export const materialsApi = {
  archiveMaterial: apiMethods.archiveMaterial,
  enrichMaterials: apiMethods.enrichMaterials,
  exportMaterialsUrl: apiMethods.exportMaterialsUrl,
  importMaterials: apiMethods.importMaterials,
  listMaterials: apiMethods.listMaterials,
  materialsImportTemplateUrl: apiMethods.materialsImportTemplateUrl,
  promoteMaterialCandidate: apiMethods.promoteMaterialCandidate,
  scaffoldSubstitutes: apiMethods.scaffoldSubstitutes,
  substructureSearch: apiMethods.substructureSearch,
  upsertMaterial: apiMethods.upsertMaterial,
} as const;

export type MaterialsApi = typeof materialsApi;
