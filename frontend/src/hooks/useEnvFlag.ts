import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

/**
 * Read one boolean Settings toggle (``GET /api/settings/env-flags``) and keep it
 * in step with the Settings dialog (``envFlagsRevision`` bumps after a save).
 *
 * ``fallback`` is used until the first response and whenever the request fails,
 * so UI behind a toggle never vanishes just because the API is slow or down.
 */
export function useEnvFlag(attr: string, fallback: boolean): boolean {
  const revision = useStore((s) => s.envFlagsRevision);
  const [value, setValue] = useState(fallback);

  useEffect(() => {
    let cancelled = false;
    api
      .getEnvFlags()
      .then((body) => {
        if (cancelled) return;
        const flag = (body.flags ?? []).find((f) => f.attr === attr);
        if (flag) setValue(Boolean(flag.value));
      })
      .catch(() => {
        /* keep the last known value */
      });
    return () => {
      cancelled = true;
    };
  }, [attr, revision]);

  return value;
}
