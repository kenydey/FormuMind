// @vitest-environment node
/**
 * The lint setup has one job: the hooks rules. Prove it does that, with code shaped like the
 * defects it found — so a config edit that quietly turns the rules off fails here, not in review.
 */
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

// frontend/ (this file is frontend/src/eslintConfig.test.ts); the project has no @types/node.
const FRONTEND_ROOT = new URL("..", import.meta.url).pathname;

async function lint(code: string): Promise<string[]> {
  const eslint = new ESLint({ cwd: FRONTEND_ROOT });
  const [result] = await eslint.lintText(code, { filePath: "src/components/Sample.tsx" });
  return result.messages.map((m) => m.ruleId ?? `parse: ${m.message}`);
}

describe("eslint hooks rules", () => {
  it("flags a callback that reads a value its dependency list omits (stale closure)", async () => {
    const rules = await lint(`
      import { useCallback } from "react";
      export function A({ onChanged }: { onChanged: (n: number) => void }) {
        return useCallback(() => onChanged(1), []);
      }
    `);
    expect(rules).toContain("react-hooks/exhaustive-deps");
  });

  it("flags a dependency rebuilt on every render", async () => {
    const rules = await lint(`
      import { useEffect } from "react";
      export function B({ items }: { items?: string[] }) {
        const list = items ?? [];
        useEffect(() => { console.info(list.length); }, [list]);
        return null;
      }
    `);
    expect(rules).toContain("react-hooks/exhaustive-deps");
  });

  it("flags a hook called conditionally", async () => {
    const rules = await lint(`
      import { useState } from "react";
      export function C({ on }: { on: boolean }) {
        if (on) { useState(0); }
        return null;
      }
    `);
    expect(rules).toContain("react-hooks/rules-of-hooks");
  });

  it("accepts the memoised / ref patterns the codebase uses", async () => {
    const rules = await lint(`
      import { useCallback, useEffect, useMemo, useRef } from "react";
      export function D({ items, onChanged }: { items?: string[]; onChanged?: (n: number) => void }) {
        const list = useMemo(() => items ?? [], [items]);
        const latest = useRef(onChanged);
        latest.current = onChanged;
        const load = useCallback(() => latest.current?.(list.length), [list]);
        useEffect(() => { load(); }, [load]);
        return null;
      }
    `);
    expect(rules).toEqual([]);
  });
});
