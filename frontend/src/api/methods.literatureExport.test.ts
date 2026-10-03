import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./http";
import { apiMethods } from "./methods";

/**
 * The BibTeX / RIS exports threw the raw response text on failure — for a FastAPI error that is
 * the JSON string `{"detail":"…"}` shown to the user as is — while every other wrapper goes
 * through readApiError.
 */
afterEach(() => vi.unstubAllGlobals());

function respond(status: number, body: string) {
  const fetchMock = vi.fn(async () => new Response(body, { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe.each([
  ["exportLiteratureBib", "bib"],
  ["exportLiteratureRis", "ris"],
] as const)("%s", (method, ext) => {
  it("returns the file text and sends the filters", async () => {
    const fetchMock = respond(200, "@article{a}");
    const text = await apiMethods[method]("p 1", { scope: "selected", collection_id: "c9" });
    expect(text).toBe("@article{a}");
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toBe(`/api/wiki/literature/p%201/export.${ext}?scope=selected&collection_id=c9`);
  });

  it("reports the server's reason, not the JSON envelope", async () => {
    respond(404, JSON.stringify({ detail: "project not found" }));
    const error = await apiMethods[method]("p1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("project not found");
    expect((error as ApiError).status).toBe(404);
  });

  it("names the route when the body says nothing", async () => {
    respond(500, "");
    const error = await apiMethods[method]("p1").catch((e: unknown) => e);
    expect((error as ApiError).message).toBe(`/api/wiki/literature/p1/export.${ext} -> 500`);
  });
});
