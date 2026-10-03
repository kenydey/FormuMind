import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import AttachmentPreview from "./AttachmentPreview";

let fetchCount = 0;
let rows: Array<Record<string, unknown>> = [];

vi.mock("../api", () => ({
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  api: {
    getWorkbenchAttachments: async () => {
      fetchCount += 1;
      return rows;
    },
    uploadWorkbenchAttachment: async () => ({}),
    deleteWorkbenchAttachment: async () => ({}),
    workbenchAttachmentDownloadUrl: (c: number, r: number, a: number) => `/dl/${c}/${r}/${a}`,
  },
}));
vi.mock("../utils/download", () => ({ downloadWithAuth: async () => "x" }));

beforeEach(() => {
  fetchCount = 0;
  rows = [{ id: 1, filename: "a.pdf", kind: "pdf", source_document_id: "s1" }];
});

/** Mirrors ``LabWorkbench``: an inline ``onChanged`` that writes a *new object* into parent state. */
function Parent() {
  const [counts, setCounts] = useState<Record<number, number>>({});
  return (
    <div>
      <span data-testid="count">{counts[7] ?? "-"}</span>
      <AttachmentPreview
        campaignId={1}
        rowId={7}
        onClose={() => undefined}
        onChanged={(n) => setCounts((prev) => ({ ...prev, 7: n }))}
      />
    </div>
  );
}

describe("AttachmentPreview", () => {
  it("fetches the list once — an unstable onChanged must not re-trigger the load", async () => {
    render(<Parent />);
    await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
    // Give a runaway fetch → setState → re-render → fetch loop time to show itself.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    expect(fetchCount).toBe(1);
    expect(screen.getByText("a.pdf")).toBeTruthy();
  });

  it("still reports the new count after a reload and refetches when the row changes", async () => {
    const onChanged = vi.fn();
    const { rerender } = render(
      <AttachmentPreview campaignId={1} rowId={7} onClose={() => undefined} onChanged={onChanged} />,
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(1));
    expect(fetchCount).toBe(1);

    rows = [];
    rerender(<AttachmentPreview campaignId={1} rowId={8} onClose={() => undefined} onChanged={onChanged} />);
    await waitFor(() => expect(onChanged).toHaveBeenLastCalledWith(0));
    expect(fetchCount).toBe(2);
  });
});
