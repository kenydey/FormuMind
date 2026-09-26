// Domain facade: tasks (P2)
import { apiMethods } from "../methods";

export const tasksApi = {
  cancelTask: apiMethods.cancelTask,
  task: apiMethods.task,
} as const;

export type TasksApi = typeof tasksApi;
