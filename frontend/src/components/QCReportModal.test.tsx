/**
 * QC report modal.
 *
 * A measurement's value is the least interesting thing about it — the method
 * it was run under and the window it was judged against are what make it
 * comparable and auditable. These tests pin that the UI never quietly implies
 * a verdict or a standard it does not have.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ExperimentSummary, QCMeasurementView, QCReportResult } from "../api";
import QCReportModal from "./QCReportModal";

const experiment = (over: Partial<ExperimentSummary> = {}): ExperimentSummary =>
  ({
    id: 1,
    domain: "anticorrosion_coating",
    label: "batch-7",
    source: "lab",
    project_id: "",
    measured: {},
    measurement_count: 0,
    created_at: null,
    ...over,
  }) as ExperimentSummary;

const measurement = (over: Partial<QCMeasurementView> = {}): QCMeasurementView =>
  ({
    metric: "salt_spray_hours",
    value: 720,
    unit: "h",
    test_method: "ASTM B117",
    spec_min: 500,
    spec_max: null,
    passed: true,
    ...over,
  }) as QCMeasurementView;

/** The file input only renders once the experiment list resolves. */
async function uploadFile(name = "r.md") {
  const button = await screen.findByRole("button", { name: /上传并解析/ });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, new File(["# report"], name, { type: "text/markdown" }));
  await userEvent.click(button);
}

function mockMeasurements(measurements: QCMeasurementView[]) {
  vi.spyOn(api, "experimentMeasurements").mockResolvedValue({
    experiment_id: 1,
    measurements,
    attachments: [],
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("QCReportModal", () => {
  it("offers the stored experiments as binding targets", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([
      experiment(),
      experiment({ id: 2, label: "batch-8", measurement_count: 3 }),
    ]);
    mockMeasurements([]);

    render(<QCReportModal />);
    await waitFor(() => expect(api.listExperiments).toHaveBeenCalled());
    expect(screen.getByRole("option", { name: /batch-7/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /已有 3 项/ })).toBeInTheDocument();
  });

  it("shows the method and spec window alongside each value", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([experiment()]);
    mockMeasurements([measurement()]);

    render(<QCReportModal />);
    await waitFor(() => expect(screen.getByText("ASTM B117")).toBeInTheDocument());
    expect(screen.getByText("≥ 500")).toBeInTheDocument();
    expect(screen.getByText("合格")).toBeInTheDocument();
  });

  it("flags a measurement recorded without a test standard", async () => {
    // Salt-spray hours under ASTM B117 and ISO 9227 are not comparable, so a
    // missing method is a caveat, not a blank cell.
    vi.spyOn(api, "listExperiments").mockResolvedValue([experiment()]);
    mockMeasurements([measurement({ test_method: "" })]);

    render(<QCReportModal />);
    await waitFor(() => expect(screen.getByText("未注明")).toBeInTheDocument());
  });

  it("distinguishes no verdict from a pass", async () => {
    // No acceptance limits means nothing judged it — rendering that as a pass
    // would assert something the report never said.
    vi.spyOn(api, "listExperiments").mockResolvedValue([experiment()]);
    mockMeasurements([
      measurement({ metric: "film_weight_gsm", spec_min: null, passed: null }),
    ]);

    render(<QCReportModal />);
    await waitFor(() => expect(screen.getByText("未判定")).toBeInTheDocument());
    expect(screen.queryByText("合格")).not.toBeInTheDocument();
  });

  it("marks an out-of-spec result", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([experiment()]);
    mockMeasurements([
      measurement({ metric: "adhesion_mpa", value: 3.2, spec_min: 5, passed: false }),
    ]);

    render(<QCReportModal />);
    await waitFor(() => expect(screen.getByText("超差")).toBeInTheDocument());
  });

  it("reports which metrics became training data", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([experiment()]);
    mockMeasurements([]);
    vi.spyOn(api, "uploadQcReport").mockResolvedValue({
      experiment_id: 1,
      source_id: "doc-1",
      measurements: [measurement()],
      measurement_count: 1,
      attached: true,
      already_attached: false,
      synced_measured: { salt_spray_hours: 720 },
      report_meta: {},
      parser: "text",
      extraction_error: null,
      message: "",
    } as QCReportResult);

    render(<QCReportModal />);
    await uploadFile("r.md");

    await waitFor(() => expect(screen.getByText(/已同步进可训练数据/)).toBeInTheDocument());
  });

  it("says when a re-upload was recognised as the same report", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([experiment()]);
    mockMeasurements([]);
    vi.spyOn(api, "uploadQcReport").mockResolvedValue({
      experiment_id: 1,
      source_id: "doc-1",
      measurements: [],
      measurement_count: 0,
      attached: true,
      already_attached: true,
      synced_measured: {},
      report_meta: {},
      parser: "text",
      extraction_error: null,
      message: "",
    } as QCReportResult);

    render(<QCReportModal />);
    await uploadFile("r.md");

    await waitFor(() => expect(screen.getByText(/未重复计入/)).toBeInTheDocument());
  });

  it("guides the user when there is nothing to bind to", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([]);
    render(<QCReportModal />);
    await waitFor(() => expect(screen.getByText(/暂无实验记录/)).toBeInTheDocument());
  });

  it("surfaces an upload failure", async () => {
    vi.spyOn(api, "listExperiments").mockResolvedValue([experiment()]);
    mockMeasurements([]);
    vi.spyOn(api, "uploadQcReport").mockRejectedValue(new Error("无法从报告提取文本"));

    render(<QCReportModal />);
    // An accepted file type: the failure comes from the mocked call, and
    // userEvent honours the input's accept filter, so a rejected extension
    // would never reach the upload handler at all.
    await uploadFile("r.md");

    await waitFor(() =>
      expect(screen.getByText("无法从报告提取文本")).toBeInTheDocument()
    );
  });
});
