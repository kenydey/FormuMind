import { describe, expect, it } from "vitest";
import { ApiError } from "./http";
import { exportFailure } from "./methods";

const res = (status: number, body: string) => new Response(body, { status });

describe("exportFailure", () => {
  it("turns a preflight block into an actionable Chinese message", async () => {
    const body = JSON.stringify({
      detail: {
        error: "publication_preflight_blocked",
        message: "publication_preflight_blocked",
        preflight: { errors: ["2 open blocking findings"], state: { open_blocking: 2, findings: [{}] } },
      },
    });
    const err = await exportFailure(res(409, body), "fallback");
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toContain("发布预检未通过：2 条阻断项未处理");
    expect(err.message).toContain("2 open blocking findings");
    expect(err.message).toContain("「发布预检」面板");
    expect(err.message).not.toContain("{");
    expect((err as ApiError).status).toBe(409);
  });

  it("copes with a block that carries no counts", async () => {
    const body = JSON.stringify({ detail: { error: "publication_preflight_blocked", preflight: {} } });
    const err = await exportFailure(res(409, body), "fallback");
    expect(err.message).toContain("存在未处理的阻断项");
  });

  it("passes plain string details through", async () => {
    const err = await exportFailure(res(404, JSON.stringify({ detail: "storm report markdown empty" })), "x");
    expect(err.message).toBe("storm report markdown empty");
  });

  it("uses an object detail's message", async () => {
    const err = await exportFailure(res(409, JSON.stringify({ detail: { message: "Wiki 未启用" } })), "x");
    expect(err.message).toBe("Wiki 未启用");
  });

  it("falls back to the raw text, then to the caller's fallback", async () => {
    expect((await exportFailure(res(502, "Bad gateway"), "fb")).message).toBe("Bad gateway");
    expect((await exportFailure(res(500, ""), "export failed (500)")).message).toBe("export failed (500)");
  });
});
