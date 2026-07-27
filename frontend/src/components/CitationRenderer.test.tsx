import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import CitationRenderer from "./CitationRenderer";
import type { CitationAnchor } from "./CitationRenderer";

// Mock scrollIntoView since jsdom doesn't implement it
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const sampleAnchors: CitationAnchor[] = [
  {
    id: "1",
    title: "Source Alpha",
    snippet: "This is snippet alpha",
    url: "https://example.com/alpha",
  },
  {
    id: "2",
    title: "Source Beta",
    snippet: "This is snippet beta",
    url: "https://example.com/beta",
  },
];

describe("CitationRenderer", () => {
  it("renders superscript citation links for [^n] markers in answer", () => {
    const answer = "This is a claim.[^1] It is supported.[^2]";
    const footnotes = "[^1]: Source Alpha citation text\n[^2]: Source Beta citation text";

    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={sampleAnchors}
      />,
    );

    // Check that superscript citation links are rendered
    const supLinks = screen.getAllByText(/\[\^[12]\]/);
    expect(supLinks).toHaveLength(2);

    // Verify they are links inside <sup>
    expect(supLinks[0].closest("sup")).toBeTruthy();
    expect(supLinks[1].closest("sup")).toBeTruthy();
  });

  it("clicking a citation scrolls to the corresponding footnote", () => {
    const answer = "A statement.[^1] More text.";
    const footnotes = "[^1]: Footnote one content";

    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={sampleAnchors}
      />,
    );

    // The footnote should be rendered with id="fn-1"
    const footnote = document.getElementById("fn-1");
    expect(footnote).toBeTruthy();

    // Click the citation link [^1]
    const citationLink = screen.getByText(/\[\^1\]/);
    fireEvent.click(citationLink);

    // scrollIntoView should have been called on the footnote element
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("renders footnote entries with id anchors", () => {
    const answer = "Text[^1]";
    const footnotes = "[^1]: First footnote\n[^2]: Second footnote";

    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={sampleAnchors}
      />,
    );

    expect(document.getElementById("fn-1")).toBeTruthy();
    expect(document.getElementById("fn-2")).toBeTruthy();
  });

  it("renders anchor cards when anchors are provided", () => {
    const answer = "Text[^1]";
    const footnotes = "[^1]: Footnote content";

    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={sampleAnchors}
      />,
    );

    // Should render the anchor title inside the footnote
    expect(screen.getByText("Source Alpha")).toBeTruthy();
    expect(screen.getByText("This is snippet alpha")).toBeTruthy();
  });

  it("handles answer text without any citations gracefully", () => {
    const answer = "Plain text with no citations.";
    const footnotes = "";

    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={[]}
      />,
    );

    // Should render the plain text
    expect(screen.getByText("Plain text with no citations.")).toBeTruthy();

    // Should not have any superscript links
    const supLinks = document.querySelectorAll("sup");
    expect(supLinks).toHaveLength(0);
  });

  it("applies highlight class on footnote hover", () => {
    const answer = "Text[^1]";
    const footnotes = "[^1]: A footnote";

    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={sampleAnchors}
      />,
    );

    const footnote = document.getElementById("fn-1")!;
    expect(footnote).toBeTruthy();

    // Initially no highlight
    expect(footnote.classList.contains("citation-footnote-highlight")).toBe(false);

    // Hover over the footnote
    fireEvent.mouseEnter(footnote);
    expect(footnote.classList.contains("citation-footnote-highlight")).toBe(true);

    // Mouse leave removes highlight
    fireEvent.mouseLeave(footnote);
    expect(footnote.classList.contains("citation-footnote-highlight")).toBe(false);
  });
});
