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
      gate_drops: {
        retrieval: { blocked_domain: 2, garbage_snippet: 0, wiki_track: 0 },
        ingest: { blocked_domain: 0, garbage_snippet: 0, wiki_track: 0 },
      },
      gate_drops_total: {
        retrieval: { blocked_domain: 5, garbage_snippet: 1, wiki_track: 0 },
        ingest: { blocked_domain: 1, garbage_snippet: 0, wiki_track: 0 },
      },
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
    expect(screen.getByTestId("retrieval-probe-gate-drops")).toHaveTextContent("本轮门禁丢弃=2");
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

  it("shows MRR and Recall@k after golden batch", async () => {
    kbGoldenEvalRun.mockResolvedValue({
      mode: "hybrid",
      top_k: 3,
      alpha: 0.3,
      total: 2,
      passed: 2,
      failed: 0,
      mrr: 0.75,
      recall_at_k: 1.0,
      results: [
        {
          question: "硅烷",
          passed: true,
          expected_keywords: ["硅烷"],
          hit_titles: ["硅烷偶联剂"],
        },
      ],
    });
    render(<RetrievalProbePanel active />);
    fireEvent.click(screen.getByTestId("retrieval-probe-golden"));
    await waitFor(() => {
      expect(screen.getByTestId("retrieval-probe-golden-mrr")).toHaveTextContent("MRR 0.750");
    });
    expect(screen.getByTestId("retrieval-probe-golden-recall")).toHaveTextContent("Recall@3 1.000");
  });
});
