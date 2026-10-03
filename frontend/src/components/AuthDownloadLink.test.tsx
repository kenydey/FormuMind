import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AuthDownloadLink from "./AuthDownloadLink";

// A plain function behind the mock (not vi.fn().mockRejectedValue): vitest 4 reports
// the rejection of a spied async function as an unhandled error even when the code
// under test awaits it inside try/catch.
const calls: Array<[string, string | undefined]> = [];
let behaviour: () => Promise<string> = async () => "f.csv";

vi.mock("../utils/download", () => ({
  downloadWithAuth: (url: string, name?: string) => {
    calls.push([url, name]);
    return behaviour();
  },
}));
vi.mock("../api", () => ({ formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)) }));

beforeEach(() => {
  calls.length = 0;
  behaviour = async () => "f.csv";
});

describe("AuthDownloadLink", () => {
  it("keeps href for copy-link but downloads through the authenticated helper", async () => {
    render(
      <AuthDownloadLink url="/api/doe/9/export" filename="doe_9.csv" testId="dl">
        导出
      </AuthDownloadLink>,
    );
    const a = screen.getByTestId("dl") as HTMLAnchorElement;
    expect(a.getAttribute("href")).toBe("/api/doe/9/export");

    const notPrevented = fireEvent.click(a); // fireEvent returns false when preventDefault() ran
    expect(notPrevented).toBe(false);
    await waitFor(() => expect(calls).toEqual([["/api/doe/9/export", "doe_9.csv"]]));
  });

  it("shows busy state, ignores a second click while busy, and recovers", async () => {
    let release!: (v: string) => void;
    behaviour = () => new Promise<string>((res) => (release = res));
    render(<AuthDownloadLink url="/u" testId="dl">下载</AuthDownloadLink>);
    const a = screen.getByTestId("dl");
    fireEvent.click(a);
    await waitFor(() => expect(a.textContent).toBe("下载中…"));
    fireEvent.click(a);
    expect(calls).toHaveLength(1);
    release("x");
    await waitFor(() => expect(a.textContent).toBe("下载"));
  });

  it("surfaces a failed download instead of failing silently (401, 404, …)", async () => {
    behaviour = async () => {
      throw new Error("Unauthorized");
    };
    render(<AuthDownloadLink url="/u" testId="dl">下载</AuthDownloadLink>);
    await act(async () => {
      fireEvent.click(screen.getByTestId("dl"));
    });
    const err = screen.getByTestId("dl-error");
    expect(err.textContent).toBe("Unauthorized");
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
