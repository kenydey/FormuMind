/**
 * QC report modal (row-level).
 *
 * A measurement's value is the least interesting thing about it — the method
 * it was run under and the window it was judged against are what make it
 * comparable and auditable. These tests pin that the UI never quietly implies
 * a verdict or a standard it does not have, and that it binds reports to a
 * specific workbench row (campaign + row passed in as props).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { QCMeasurementView, QCReportResult } from "../api";
import QCReportModal from "./QCReportModal";

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

async function uploadFile(name = "r.md") {
  const button = await screen.findByRole("button", { name: /上传并解析/ });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input, new File(["# report"], name, { type: "text/markdown" }));
  await userEvent.click(button);
}

function mockExisting(measurements: QCMeasurementView[]) {
  vi.spyOn(api, "getWorkbenchRowMeasurements").mockResolvedValue({
    experiment_id: 1,
    measurements,
    attachments: [],
  });
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

function renderModal() {
  return render(
    <QCReportModal campaignId={1} rowId={1} onClose={vi.fn()} />
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("QCReportModal", () => {
  it("shows the method and spec window alongside each value", async () => {
    mockExisting([]);
    mockUpload({ measurements: [measurement()], measurement_count: 1 });

    renderModal();
    await uploadFile();
    await waitFor(() => expect(screen.getByText("ASTM B117")).toBeInTheDocument());
    expect(screen.getByText("≥ 500")).toBeInTheDocument();
    expect(screen.getByText("合格")).toBeInTheDocument();
  });

  it("flags a measurement recorded without a test standard", async () => {
    mockExisting([]);
    mockUpload({ measurements: [measurement({ test_method: "" })], measurement_count: 1 });

    renderModal();
    await uploadFile();
    await waitFor(() => expect(screen.getByText("未注明")).toBeInTheDocument());
  });

  it("distinguishes no verdict from a pass", async () => {
    mockExisting([]);
    mockUpload({
      measurements: [
        measurement({ metric: "film_weight_gsm", spec_min: null, passed: null }),
      ],
      measurement_count: 1,
    });

    renderModal();
    await uploadFile();
    await waitFor(() => expect(screen.getByText("未判定")).toBeInTheDocument());
    expect(screen.queryByText("合格")).not.toBeInTheDocument();
  });

  it("marks an out-of-spec result", async () => {
    mockExisting([]);
    mockUpload({
      measurements: [
        measurement({ metric: "adhesion_mpa", value: 3.2, spec_min: 5, passed: false }),
      ],
      measurement_count: 1,
    });

    renderModal();
    await uploadFile();
    await waitFor(() => expect(screen.getByText("超差")).toBeInTheDocument());
  });

  it("reports which metrics became training data", async () => {
    mockExisting([]);
    mockUpload({
      measurements: [measurement()],
      measurement_count: 1,
      synced_measured: { salt_spray_hours: 720 },
    });

    renderModal();
    await uploadFile();
    await waitFor(() =>
      expect(screen.getByText(/已同步进可训练数据/)).toBeInTheDocument()
    );
  });

  it("says when a re-upload was recognised as the same report", async () => {
    mockExisting([]);
    mockUpload({ already_attached: true });

    renderModal();
    await uploadFile();
    await waitFor(() => expect(screen.getByText(/未重复计入/)).toBeInTheDocument());
  });

  it("shows pre-existing measurements for the row", async () => {
    mockExisting([measurement()]);

    renderModal();
    await waitFor(() => expect(screen.getByText("ASTM B117")).toBeInTheDocument());
  });

  it("surfaces an upload failure", async () => {
    mockExisting([]);
    vi.spyOn(api, "uploadQcReport").mockRejectedValue(new Error("无法从报告提取文本"));

    renderModal();
    await uploadFile();
    await waitFor(() =>
      expect(screen.getByText("无法从报告提取文本")).toBeInTheDocument()
    );
  });
});
