/**
 * The `testId` contract on the shared Modal.
 *
 * These hooks exist for the out-of-process Playwright drivers in `scripts/`,
 * which previously located things with `.fixed.inset-0.z-50 button` — a
 * selector that broke when this title bar grew from one button to four, since
 * `.first` started resolving to minimize and maximize/restore share the glyph
 * `□`. So the thing worth pinning is that each control is *individually*
 * addressable, not merely that some attribute exists.
 *
 * Queried with `container.querySelector` rather than `getByTestId` on purpose:
 * the suite's convention is role/text queries, and testids are for the
 * external drivers. This asserts the prop contract without inviting the tests
 * to start selecting by testid.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import Modal from "./Modal";

function renderModal(props: Partial<React.ComponentProps<typeof Modal>> = {}) {
  return render(
    <Modal title="测试弹窗" open onClose={() => {}} testId="modal-demo" {...props}>
      <p>body</p>
    </Modal>
  );
}

describe("Modal testId contract", () => {
  it("gives every window control its own hook", () => {
    const { container } = renderModal({ onSave: () => {} });
    for (const id of [
      "modal-demo",
      "modal-demo-overlay",
      "modal-demo-close",
      "modal-demo-minimize",
      "modal-demo-maximize",
      "modal-demo-save",
    ]) {
      expect(container.ownerDocument.querySelector(`[data-testid="${id}"]`)).not.toBeNull();
    }
  });

  it("distinguishes minimize from close, which a text filter cannot", async () => {
    const onClose = vi.fn();
    const { container } = renderModal({ onClose });
    const doc = container.ownerDocument;

    await userEvent.click(doc.querySelector('[data-testid="modal-demo-minimize"]')!);
    // Minimizing must not close: the old `.first` selector conflated them.
    expect(onClose).not.toHaveBeenCalled();
    expect(doc.querySelector('[data-testid="modal-demo-minimized"]')).not.toBeNull();

    await userEvent.click(doc.querySelector('[data-testid="modal-demo-close"]')!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("omits the save hook when no save handler is given", () => {
    const { container } = renderModal();
    expect(
      container.ownerDocument.querySelector('[data-testid="modal-demo-save"]')
    ).toBeNull();
  });

  it("emits no testid attributes at all when testId is absent", () => {
    const { container } = render(
      <Modal title="无标识" open onClose={() => {}}>
        <p>body</p>
      </Modal>
    );
    expect(
      container.ownerDocument.querySelectorAll("[data-testid]").length
    ).toBe(0);
  });

  it("still renders its title and body", () => {
    renderModal();
    expect(screen.getByRole("heading", { name: "测试弹窗" })).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });
});
