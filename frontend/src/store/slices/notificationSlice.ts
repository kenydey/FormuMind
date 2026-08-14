import type { NotificationKind } from "../notifications";
import type { SliceGet, SliceSet } from "../sliceTypes";
import type { AppState } from "../types";

export function createNotificationSlice(set: SliceSet, _get: SliceGet) {
  return {
    clearError: () =>
      set((draft) => {
        draft.error = null;
      }),

    /**
     * Hide a notification. This is display-only by design: it never touches a
     * busy flag and never cancels the underlying task.
     *
     * `awaitTaskStream` does accept an `AbortSignal`, but no call site passes
     * one, and aborting the client stream would not stop the server-side task
     * anyway (search and KB ingest both run there). A `×` that claimed to
     * cancel while only stopping the listener would be worse than one that
     * honestly means "stop showing me this" — the user would think they had
     * collapsed a card and would actually have lost a search.
     *
     * Run state stays visible in the left column meanwhile: the search button
     * label, the deep-research button label, and the per-row KB badges.
     */
    dismissNotification: (kind: NotificationKind) =>
      set((draft) => {
        // `error` exists only to be displayed and has no writer loop, so
        // clearing the field IS the dismissal. Every other kind is driven by
        // a field with other consumers or a per-frame writer, so it must be
        // flagged instead — see the header of store/notifications.ts.
        if (kind === "error") {
          draft.error = null;
          return;
        }
        draft.notificationsDismissed[kind] = true;
      }),
  } as Pick<AppState, "clearError" | "dismissNotification">;
}
