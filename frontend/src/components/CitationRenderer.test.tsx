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

describe("CitationRenderer markdown answer (排版修复)", () => {
  it("renders markdown formatting in the answer while keeping sup citation links", () => {
    const answer =
      "## 标题\n\n这是**加粗**文本。[^1]\n\n- 列表项一\n- 列表项二[^2]";
    const footnotes = "[^1]: 脚注一\n[^2]: 脚注二";
    render(<CitationRenderer answer={answer} footnotes={footnotes} anchors={[]} />);

    // Markdown 结构正常渲染
    expect(document.querySelector(".citation-answer h2")).toBeTruthy();
    expect(document.querySelector(".citation-answer strong")).toHaveTextContent("加粗");
    expect(document.querySelectorAll(".citation-answer ul li")).toHaveLength(2);
    // 上标引用链接仍在
    const sups = document.querySelectorAll(".citation-answer sup.citation-sup");
    expect(sups).toHaveLength(2);
    // 无裸 markdown 符号残留
    expect(document.querySelector(".citation-answer")?.textContent).not.toContain("**");
  });

  it("renders GFM tables in the answer", () => {
    const answer =
      "结果如下[^1]：\n\n| 专利号 | 标题 |\n| --- | --- |\n| CN123 | 测试 |\n";
    render(<CitationRenderer answer={answer} footnotes="" anchors={[]} />);
    expect(document.querySelector(".citation-answer table")).toBeTruthy();
    expect(
      document.querySelectorAll(".citation-answer sup.citation-sup"),
    ).toHaveLength(1);
  });

  it("keeps [^n] literal inside fenced code blocks", () => {
    const answer = "示例：\n\n```\ncode [^1] here\n```\n\n正文引用[^2]。";
    render(<CitationRenderer answer={answer} footnotes="" anchors={[]} />);
    const sups = document.querySelectorAll(".citation-answer sup.citation-sup");
    expect(sups).toHaveLength(1);
    expect(sups[0].textContent).toBe("[^2]");
    // 代码块内原文保留
    expect(document.querySelector(".citation-answer pre")?.textContent).toContain(
      "[^1]",
    );
  });

  it("clicking a rehype-generated sup link scrolls to the footnote", () => {
    const answer = "陈述[^1]。";
    const footnotes = "[^1]: 脚注内容";
    render(<CitationRenderer answer={answer} footnotes={footnotes} anchors={[]} />);
    fireEvent.click(screen.getByText("[^1]"));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });
});

describe("CitationRenderer page badges (W3-14)", () => {
  it("shows static page badge when no jump handler", () => {
    const answer = "A claim.[^1]";
    const footnotes = "[^1]: cited text";
    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={[{ id: "1", title: "Paper", snippet: "snip", page: 7 }]}
      />
    );
    const badge = screen.getByTestId("citation-page-badge-1");
    expect(badge).toHaveTextContent("p.7");
  });

  it("clickable badge calls onJumpToSource with sourceId+page", () => {
    const onJump = vi.fn();
    const answer = "A claim.[^1]";
    const footnotes = "[^1]: cited text";
    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={[{ id: "1", title: "Paper", snippet: "snip", page: 3, sourceId: "src-9" }]}
        onJumpToSource={onJump}
      />
    );
    fireEvent.click(screen.getByTestId("citation-page-jump-1"));
    expect(onJump).toHaveBeenCalledWith("src-9", 3);
  });

  it("no badge when page is absent", () => {
    const answer = "A claim.[^1]";
    const footnotes = "[^1]: cited text";
    render(
      <CitationRenderer
        answer={answer}
        footnotes={footnotes}
        anchors={[{ id: "1", title: "Paper", snippet: "snip" }]}
      />
    );
    expect(screen.queryByTestId("citation-page-badge-1")).toBeNull();
    expect(screen.queryByTestId("citation-page-jump-1")).toBeNull();
  });
});
