import { useEffect, useState } from "react";
import { api } from "../api";

export type EngineAvailability = Record<string, boolean>;

/** Fetch GET /api/meta engines{} probes; unknown keys default to true (optimistic). */
export function useEngineAvailability(): EngineAvailability {
  const [map, setMap] = useState<EngineAvailability>({});

  useEffect(() => {
    let cancelled = false;
    void api
      .getMeta()
      .then((meta) => {
        if (cancelled) return;
        const next: EngineAvailability = {};
        for (const [k, v] of Object.entries(meta.engines ?? {})) {
          next[k] = Boolean(v?.available);
        }
        setMap(next);
      })
      .catch(() => {
        /* leave empty → treat as unknown/available for offline UI */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return map;
}

/** True when probe says available, or when probe has not loaded yet. */
export function engineOk(map: EngineAvailability, id: string): boolean {
  if (!(id in map)) return true;
  return map[id] !== false;
}
