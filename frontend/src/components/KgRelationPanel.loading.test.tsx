import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import KgRelationPanel from "./KgRelationPanel";

/** ``kgResolve`` stays pending until the test releases it (an in-flight request). */
let releaseResolve: () => void = () => undefined;

describe("KgRelationPanel loading state", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "kgFeedbackReport").mockResolvedValue({ alert: null } as never);
    vi.spyOn(api, "kgFeedbackStats").mockResolvedValue({
      measured_total: 0,
      measured_performance: 0,
    } as never);
    vi.spyOn(api, "kgResolve").mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseResolve = () =>
            resolve({
              query: "epoxy",
              chemicals: [],
              trade_products: [],
              expanded_entity_ids: [],
              top_relations: [],
              mode: "semantic",
              trade_only: false,
              interpretation: "",
            } as never);
        }),
    );
  });

  it("does not stay on 加载中… when the query is cleared while a request is in flight", async () => {
    const { rerender, container } = render(<KgRelationPanel query="epoxy" />);
    // 600 ms debounce, then the request starts and the panel shows its spinner.
    await screen.findByText("加载中…", {}, { timeout: 2500 });

    // The user empties the search box. The in-flight run is cancelled, so nobody
    // would ever switch ``loading`` off again — the panel hung on 加载中… for good.
    rerender(<KgRelationPanel query="" />);
    releaseResolve();
    await Promise.resolve();

    expect(screen.queryByText("加载中…")).toBeNull();
    expect(container.firstChild).toBeNull();
  });
});
