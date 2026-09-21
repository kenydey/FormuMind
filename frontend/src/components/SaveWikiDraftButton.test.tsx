import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";
import SaveWikiDraftButton from "./SaveWikiDraftButton";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: {
      ...actual.api,
      saveWikiDraft: vi.fn(),
    },
  };
});

describe("SaveWikiDraftButton S4", () => {
  beforeEach(() => {
    useStore.setState({
      activeProjectId: "proj-s4",
      openSettings: vi.fn(),
    } as never);
    vi.mocked(api.saveWikiDraft).mockResolvedValue({
      ok: true,
      path: "queries/project-proj-s4-salt.md",
      disclaimer: "draft_not_claims",
      flags: ["unreviewed", "draft"],
    });
  });

  it("saves assistant answer as wiki draft", async () => {
    const user = userEvent.setup();
    render(
      <SaveWikiDraftButton
        message={{
          role: "assistant",
          content: "建议盐雾 ≥500h。",
          citations: [{ title: "p", source: "literature", identifier: "doi:1", snippet: "x", relevance: 0.9 }],
        }}
        question="盐雾？"
        origin="chat"
      />,
    );
    await user.click(screen.getByTestId("save-wiki-draft-btn"));
    await waitFor(() => {
      expect(api.saveWikiDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          project_id: "proj-s4",
          question: "盐雾？",
          answer_markdown: "建议盐雾 ≥500h。",
          origin: "chat",
        }),
      );
    });
    expect(await screen.findByTestId("save-wiki-draft-ok")).toHaveTextContent(/draft_not_claims/);
  });

  it("shows settings CTA when flag off", async () => {
    const user = userEvent.setup();
    vi.mocked(api.saveWikiDraft).mockRejectedValue(
      new Error("wiki_chat_save_draft is false"),
    );
    render(
      <SaveWikiDraftButton
        message={{ role: "assistant", content: "answer" }}
        question="q"
      />,
    );
    await user.click(screen.getByTestId("save-wiki-draft-btn"));
    expect(await screen.findByTestId("save-wiki-draft-open-env")).toBeInTheDocument();
  });
});
