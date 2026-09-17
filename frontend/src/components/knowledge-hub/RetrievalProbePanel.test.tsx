import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import RetrievalProbePanel from "./RetrievalProbePanel";

const kbQueryTest = vi.fn();
const kbGoldenEvalRun = vi.fn();
const kbRetrievalSettings = vi.fn();

vi.mock("../../api", () => ({
  api: {
    kbQueryTest: (...args: unknown[]) => kbQueryTest(...args),
    kbGoldenEvalRun: (...args: unknown[]) => kbGoldenEvalRun(...args),
    kbRetrievalSettings: (...args: unknown[]) => kbRetrievalSettings(...args),
  },
  formatApiError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}));

describe("RetrievalProbePanel", () => {
  beforeEach(() => {
    kbQueryTest.mockReset();
    kbGoldenEvalRun.mockReset();
    kbRetrievalSettings.mockReset();
    kbRetrievalSettings.mockResolvedValue({
      kb_hybrid_alpha: 0.42,
      kb_recommend_use_hybrid: true,
      kb_recommend_include_global: true,
      kb_recommend_top_k: 4,
      kb_recommend_rerank_enabled: false,
    });
    useStore.setState({ activeProjectId: "proj-demo" } as never);
  });

  it("loads shared server alpha when active", async () => {
    render(<RetrievalProbePanel active />);
    await waitFor(() => {
      expect(screen.getByTestId("retrieval-probe-server-alpha")).toHaveTextContent("0.42");
    });
    expect(screen.getByTestId("retrieval-probe-alpha")).toHaveValue(0.42);
    expect(kbRetrievalSettings).toHaveBeenCalled();
  });

  it("renders table headers after a successful probe run", async () => {
    kbQueryTest.mockResolvedValue({
      query: "硅烷偶联剂",
      mode: "hybrid",
      params: {
        top_k: 10,
        alpha: 0.3,
        project_id: "proj-demo",
        include_global: true,
        rerank_applied: false,
      },
      vector_mode: "semantic",
      elapsed_ms: 12,
      hits: [
        {
          rank: 1,
          title: "硅烷偶联剂 · 表面处理",
          snippet: "硅烷偶联剂用于金属表面处理",
          bm25_score: 0.8,
          cosine_score: 0.4,
          hybrid_score: 0.52,
          relevance: 0.52,
          rerank_score: null,
          rank_before_rerank: 1,
        },
      ],
      warning: null,
    });

    render(<RetrievalProbePanel active />);
    expect(screen.getByTestId("hub-retrieval-pane")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("retrieval-probe-run"));
    await waitFor(() => {
      expect(screen.getByTestId("retrieval-probe-table")).toBeInTheDocument();
    });
    expect(screen.getByText("bm25")).toBeInTheDocument();
    expect(screen.getByText("cosine")).toBeInTheDocument();
    expect(screen.getByText("hybrid")).toBeInTheDocument();
    expect(screen.getByText("rerank")).toBeInTheDocument();
    expect(kbQueryTest).toHaveBeenCalled();
  });

  it("shows empty-query error without calling API", async () => {
    render(<RetrievalProbePanel active />);
    fireEvent.change(screen.getByTestId("retrieval-probe-input"), {
      target: { value: "   " },
    });
    const btn = screen.getByTestId("retrieval-probe-run");
    expect(btn).toBeDisabled();
    expect(kbQueryTest).not.toHaveBeenCalled();
  });
});
