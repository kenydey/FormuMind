import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../../store";
import HubReportsPlaceholderPane from "./HubReportsPlaceholderPane";

describe("HubReportsPlaceholderPane", () => {
  beforeEach(() => {
    useStore.setState({ activeProjectId: "proj-demo" } as never);
  });

  it("mentions dossier pack foundation and active project", () => {
    render(<HubReportsPlaceholderPane />);
    expect(screen.getByTestId("hub-reports-pane")).toBeInTheDocument();
    expect(screen.getByTestId("hub-reports-dossier-hint").textContent).toMatch(/proj-demo/);
    expect(screen.getByText(/DossierPack/i)).toBeInTheDocument();
    expect(screen.getByText(/主读卷宗 S1 \+ S6 \+ S8/)).toBeInTheDocument();
  });
});
