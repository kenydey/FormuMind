import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type PlatformHealth } from "../api";
import InfraHealthBanner from "./InfraHealthBanner";

const OK: PlatformHealth = {
  status: "ok",
  database: { ok: true, scheme: "sqlite" },
  task_broker: { required: false, reachable: true },
  parsers: { pdf: true },
  datalab: { required: true, reachable: true, ledger_mode: "eln" },
};

const DEGRADED: PlatformHealth = {
  status: "degraded",
  database: { ok: true, scheme: "sqlite" },
  task_broker: { required: false, reachable: true },
  parsers: { pdf: true },
  datalab: { required: true, reachable: false, hint: "ELN offline", ledger_mode: "eln_required_down" },
};

const LOCAL: PlatformHealth = {
  status: "ok",
  database: { ok: true, scheme: "sqlite" },
  task_broker: { required: false, reachable: true },
  parsers: { pdf: true },
  datalab: {
    required: false,
    reachable: false,
    ledger_mode: "local",
    hint: "已启用本地 sqlite 台账（soft-degrade）",
  },
};

describe("InfraHealthBanner", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("stays hidden when /health is ok with ELN", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue(OK);
    const { container } = render(<InfraHealthBanner />);
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="infra-health-banner"]')).toBeNull();
    expect(container.querySelector('[data-testid="infra-health-local-ledger"]')).toBeNull();
  });

  it("surfaces datalab outage when degraded", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue(DEGRADED);
    render(<InfraHealthBanner />);
    expect(await screen.findByTestId("infra-health-banner")).toBeTruthy();
    expect(screen.getByText(/ELN offline/)).toBeTruthy();
  });

  it("shows soft local-ledger advisory when optional ELN is down", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue(LOCAL);
    render(<InfraHealthBanner />);
    expect(await screen.findByTestId("infra-health-local-ledger")).toBeTruthy();
    expect(screen.getByText(/本地台账/)).toBeTruthy();
    expect(screen.queryByTestId("infra-health-banner")).toBeNull();
  });
});
