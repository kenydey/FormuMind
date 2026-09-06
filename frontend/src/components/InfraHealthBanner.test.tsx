import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type PlatformHealth } from "../api";
import InfraHealthBanner from "./InfraHealthBanner";

const OK: PlatformHealth = {
  status: "ok",
  database: { ok: true, scheme: "sqlite" },
  task_broker: { required: false, reachable: true },
  parsers: { pdf: true },
  datalab: { required: true, reachable: true },
};

const DEGRADED: PlatformHealth = {
  status: "degraded",
  database: { ok: true, scheme: "sqlite" },
  task_broker: { required: false, reachable: true },
  parsers: { pdf: true },
  datalab: { required: true, reachable: false, hint: "ELN offline" },
};

describe("InfraHealthBanner", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("stays hidden when /health is ok", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue(OK);
    const { container } = render(<InfraHealthBanner />);
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="infra-health-banner"]')).toBeNull();
  });

  it("surfaces datalab outage when degraded", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue(DEGRADED);
    render(<InfraHealthBanner />);
    expect(await screen.findByTestId("infra-health-banner")).toBeTruthy();
    expect(screen.getByText(/ELN offline/)).toBeTruthy();
  });
});
