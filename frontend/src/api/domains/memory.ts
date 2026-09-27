// Domain facade: memory (W3-8)
import { apiMethods } from "../methods";

export const memoryApi = {
  listMemories: apiMethods.listMemories,
  deleteMemory: apiMethods.deleteMemory,
} as const;

export type MemoryApi = typeof memoryApi;
