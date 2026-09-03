/**
 * The notification primitive.
 *
 * The close button is the whole point of this component existing, so it gets
 * the most attention: it must always be present (the prop is mandatory), it
 * must be reachable by accessible name so the stack's tests can address one
 * card among several, and it must fire exactly once per click.
 *
 * The tone assertions look like testing CSS, and they are — deliberately. The
 * cards these classes came from were spread across two panels; pinning the
 * palette is what stops a future refactor from silently drifting the colours
 * apart again.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import NotificationCard from "./NotificationCard";

describe("NotificationCard", () => {
  it("renders the title and message", () => {
    render(<NotificationCard tone="info" title="实时检索" message="检索中…" onDismiss={vi.fn()} />);
    expect(screen.getByText("实时检索")).toBeTruthy();
    expect(screen.getByText("检索中…")).toBeTruthy();
  });

  it("always exposes a close button by accessible name", async () => {
    const onDismiss = vi.fn();
    render(<NotificationCard tone="info" title="实时检索" onDismiss={onDismiss} />);
    const close = screen.getByRole("button", { name: "关闭" });
    await userEvent.click(close);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("renders a progress bar only when progress is given", () => {
    const { container, rerender } = render(
      <NotificationCard tone="info" title="t" onDismiss={vi.fn()} />
    );
    expect(container.querySelector(".bg-edge")).toBeNull();

    rerender(<NotificationCard tone="info" title="t" progress={null} onDismiss={vi.fn()} />);
    expect(container.querySelector(".bg-edge")).toBeNull();

    rerender(<NotificationCard tone="info" title="t" progress={0.5} onDismiss={vi.fn()} />);
    const bar = container.querySelector(".bg-edge > div") as HTMLElement;
    expect(bar).toBeTruthy();
    expect(bar.style.width).toBe("50%");
  });

  it("clamps progress into 0..100%", () => {
    const { container, rerender } = render(
      <NotificationCard tone="info" title="t" progress={2} onDismiss={vi.fn()} />
    );
    expect((container.querySelector(".bg-edge > div") as HTMLElement).style.width).toBe("100%");

    rerender(<NotificationCard tone="info" title="t" progress={-1} onDismiss={vi.fn()} />);
    expect((container.querySelector(".bg-edge > div") as HTMLElement).style.width).toBe("0%");
  });

  it("shows detail inline by default and behind a toggle when disclosure is set", async () => {
    const { rerender } = render(
      <NotificationCard
        tone="neutral"
        title="质量过滤"
        detail={<span>剔除明细</span>}
        onDismiss={vi.fn()}
      />
    );
    expect(screen.getByText("剔除明细")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "展开明细" })).toBeNull();

    rerender(
      <NotificationCard
        tone="neutral"
        title="质量过滤"
        detail={<span>剔除明细</span>}
        disclosure
        onDismiss={vi.fn()}
      />
    );
    expect(screen.queryByText("剔除明细")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "展开明细" }));
    expect(screen.getByText("剔除明细")).toBeTruthy();
  });

  it("does not render a disclosure toggle when there is no detail", () => {
    render(<NotificationCard tone="info" title="t" disclosure onDismiss={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "展开明细" })).toBeNull();
  });

  it("keeps the error tone on rose", () => {
    const { container } = render(
      <NotificationCard tone="error" title="⚠ 出错" message="502" onDismiss={vi.fn()} />
    );
    const box = container.firstElementChild as HTMLElement;
    expect(box.className).toContain("border-rose-500/40");
    expect(box.className).toContain("bg-rose-500/10");
  });

  it("gives each tone a distinct box style", () => {
    const seen = new Set<string>();
    for (const tone of ["info", "success", "warn", "error", "neutral", "violet"] as const) {
      const { container, unmount } = render(
        <NotificationCard tone={tone} title="t" onDismiss={vi.fn()} />
      );
      seen.add((container.firstElementChild as HTMLElement).className);
      unmount();
    }
    expect(seen.size).toBe(6);
  });

  it("pulses only while asked to", () => {
    const { container, rerender } = render(
      <NotificationCard tone="info" title="t" message="m" progress={0.3} onDismiss={vi.fn()} />
    );
    expect(container.querySelector(".animate-pulse")).toBeNull();

    rerender(
      <NotificationCard tone="info" title="t" message="m" progress={0.3} pulse onDismiss={vi.fn()} />
    );
    expect(container.querySelector(".animate-pulse")).toBeTruthy();
  });
});
