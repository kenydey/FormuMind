/**
 * QC report modal.
 *
 * A measurement's value is the least interesting thing about it — the method
 * it was run under and the window it was judged against are what make it
 * comparable and auditable. These tests pin that the UI never quietly implies
 * a verdict or a standard it does not have, and that it binds reports to the
 * workbench rows (campaign + row) the lab bench actually shows.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type {
  QCMeasurementView,
  QCReportResult,
  WorkbenchCampaignSummary,
  WorkbenchRow,
} from "../api";
import QCReportModal from "./QCReportModal";

const campaign = (
  over: Partial<WorkbenchCampaignSummary> = {}
): WorkbenchCampaignSummary =>
  ({
    id: 1,
    name: "镁合金 DOE",
    status: "IN_PROGRESS",
    strategy: "CCD",
    row_count: 2,
    project_id: null,
    ...over,
  }) as WorkbenchCampaignSummary;

const row = (over: Partial<WorkbenchRow> = {}): WorkbenchRow =>
  ({
    id: 1,
    campaign_id: 1,
    status: "Completed",
    planned_params: {},
    actual_params: {},
    measurements: { salt_spray_hours: 720 },
    ...over,
  }) as WorkbenchRow;

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

/** The file input only renders once the campaign list resolves. */
async function uploadFile(name = "r.md") {
  const button = await screen.findByRole("button", { name: /上传并解析/ });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, new File(["# report"], name, { type: "text/markdown" }));
  await userEvent.click(button);
}

function mockCampaigns(list: WorkbenchCampaignSummary[]) {
  vi.spyOn(api, "listWorkbenchCampaigns").mockResolvedValue(list);
}

function mockRows(rows: WorkbenchRow[]) {
  vi.spyOn(api, "getWorkbenchCampaign").mockResolvedValue({
    campaign_id: 1,
    name: "c",
    strategy: "CCD",
    status: "IN_PROGRESS",
    objectives_snapshot: [],
    loop_history: [],
    rows,
  } as any);
}

function mockUpload(result: Partial<QCReportResult>) {
  vi.spyOn(api, "uploadQcReport").mockResolvedValue({
    experiment_id: 1,
    source_id: "doc-1",
    measurements: [],
    measurement_count: 0,
    attached: true,
    already_attached: false,
    synced_measured: {},
    report_meta: {},
    parser: "text",
    extraction_error: null,
    message: "",
    ...result,
  } as QCReportResult);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("QCReportModal", () => {
  it("offers workbench rows as binding targets", async () => {
    mockCampaigns([campaign(), campaign({ id: 2, name: "另一批", row_count: 3 })]);
    mockRows([row(), row({ id: 2, status: "Pending", measurements: {} })]);

    render(<QCReportModal />);
    await waitFor(() => expect(api.listWorkbenchCampaigns).toHaveBeenCalled());
    expect(screen.getByRole("option", { name: /镁合金 DOE/ })).toBeInTheDocument();
    // Rows load async once a campaign is selected.
    expect(await screen.findByRole("option", { name: /行 #1/ })).toBeInTheDocument();
  });

  it("shows the method and spec window alongside each value", async () => {
    mockCampaigns([campaign()]);
    mockRows([row()]);
    mockUpload({ measurements: [measurement()], measurement_count: 1 });

    render(<QCReportModal />);
    await uploadFile();
    await waitFor(() => expect(screen.getByText("ASTM B117")).toBeInTheDocument());
    expect(screen.getByText("≥ 500")).toBeInTheDocument();
    expect(screen.getByText("合格")).toBeInTheDocument();
  });

  it("flags a measurement recorded without a test standard", async () => {
    mockCampaigns([campaign()]);
    mockRows([row()]);
    mockUpload({ measurements: [measurement({ test_method: "" })], measurement_count: 1 });

    render(<QCReportModal />);
    await uploadFile();
    await waitFor(() => expect(screen.getByText("未注明")).toBeInTheDocument());
  });

  it("distinguishes no verdict from a pass", async () => {
    mockCampaigns([campaign()]);
    mockRows([row()]);
    mockUpload({
      measurements: [
        measurement({ metric: "film_weight_gsm", spec_min: null, passed: null }),
      ],
      measurement_count: 1,
    });

    render(<QCReportModal />);
    await uploadFile();
    await waitFor(() => expect(screen.getByText("未判定")).toBeInTheDocument());
    expect(screen.queryByText("合格")).not.toBeInTheDocument();
  });

  it("marks an out-of-spec result", async () => {
    mockCampaigns([campaign()]);
    mockRows([row()]);
    mockUpload({
      measurements: [
        measurement({ metric: "adhesion_mpa", value: 3.2, spec_min: 5, passed: false }),
      ],
      measurement_count: 1,
    });

    render(<QCReportModal />);
    await uploadFile();
    await waitFor(() => expect(screen.getByText("超差")).toBeInTheDocument());
  });

  it("reports which metrics became training data", async () => {
    mockCampaigns([campaign()]);
    mockRows([row()]);
    mockUpload({
      measurements: [measurement()],
      measurement_count: 1,
      synced_measured: { salt_spray_hours: 720 },
    });

    render(<QCReportModal />);
    await uploadFile();
    await waitFor(() =>
      expect(screen.getByText(/已同步进可训练数据/)).toBeInTheDocument()
    );
  });

  it("says when a re-upload was recognised as the same report", async () => {
    mockCampaigns([campaign()]);
    mockRows([row()]);
    mockUpload({ already_attached: true });

    render(<QCReportModal />);
    await uploadFile();
    await waitFor(() => expect(screen.getByText(/未重复计入/)).toBeInTheDocument());
  });

  it("guides the user when there is nothing to bind to", async () => {
    mockCampaigns([]);
    render(<QCReportModal />);
    await waitFor(() => expect(screen.getByText(/暂无实验台账/)).toBeInTheDocument());
  });

  it("surfaces an upload failure", async () => {
    mockCampaigns([campaign()]);
    mockRows([row()]);
    vi.spyOn(api, "uploadQcReport").mockRejectedValue(new Error("无法从报告提取文本"));

    render(<QCReportModal />);
    await uploadFile();
    await waitFor(() =>
      expect(screen.getByText("无法从报告提取文本")).toBeInTheDocument()
    );
  });
});
