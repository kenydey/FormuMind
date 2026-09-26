import { beforeEach, describe, expect, it } from "vitest";
import { getApiToken, setApiToken } from "./api";

describe("getApiToken runtime-only", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns null when nothing stored", () => {
    expect(getApiToken()).toBeNull();
  });

  it("reads token saved via setApiToken", () => {
    setApiToken("  secret-runtime-token  ");
    expect(getApiToken()).toBe("secret-runtime-token");
  });

  it("does not fall back to import.meta.env.VITE_API_TOKEN", () => {
    // Even if a build once baked VITE_API_TOKEN, getApiToken must ignore it.
    // (We cannot set import.meta.env here; absence of env read is the contract.)
    expect(getApiToken()).toBeNull();
    setApiToken("from-settings");
    expect(getApiToken()).toBe("from-settings");
  });
});
