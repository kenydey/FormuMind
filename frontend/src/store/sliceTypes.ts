import type { AppState } from "./types";

export type SliceSet = (fn: (draft: AppState) => void) => void;
export type SliceGet = () => AppState;
