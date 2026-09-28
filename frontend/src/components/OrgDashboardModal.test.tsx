import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import OrgDashboardModal from "./OrgDashboardModal";

vi.mock("./OrganizationDashboard", () => ({
  default: () => <div data-testid="org-dashboard-stub" />,
}));

describe("OrgDashboardModal (Wave 0: standalone entry)", () => {
  it("opens and closes via store toggle", () => {
    useStore.setState({ orgOpen: false } as never);
    const { rerender } = render(<OrgDashboardModal />);
    expect(screen.queryByTestId("modal-org")).not.toBeInTheDocument();
    useStore.getState().toggleOrg();
    rerender(<OrgDashboardModal />);
    expect(screen.getByTestId("modal-org")).toBeInTheDocument();
    expect(screen.getByTestId("org-dashboard-stub")).toBeInTheDocument();
  });
});
