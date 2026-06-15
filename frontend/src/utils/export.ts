// Formula export helpers: copy JSON, download CSV, and generate a one-page PDF
// report card. jsPDF + autotable are heavy (~400KB) so they are dynamically
// imported only when the user actually requests a PDF, keeping them out of the
// main bundle.
import type { Formulation } from "../api";

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function slug(name: string): string {
  return name.replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "").toLowerCase() || "formula";
}

export async function copyFormulaJson(form: Formulation): Promise<void> {
  await navigator.clipboard.writeText(JSON.stringify(form, null, 2));
}

export function formulaToCsv(form: Formulation): string {
  const lines: string[] = [];
  lines.push(`# Formulation,${form.name}`);
  lines.push(`# Domain,${form.domain}`);
  if (form.score != null) lines.push(`# Score,${form.score}`);
  lines.push("");
  lines.push("Ingredient,Role,Formula,Weight %");
  for (const ing of form.ingredients) {
    const cells = [ing.name, ing.role, ing.formula ?? "", String(ing.weight_pct)];
    lines.push(cells.map((c) => (/[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(","));
  }
  lines.push("");
  lines.push("Predicted property,Value,Std");
  for (const [k, v] of Object.entries(form.predicted)) {
    const std = form.predicted_std?.[k];
    lines.push(`${k},${v},${std ?? ""}`);
  }
  return lines.join("\n");
}

export function downloadFormulaCsv(form: Formulation): void {
  const blob = new Blob([formulaToCsv(form)], { type: "text/csv;charset=utf-8" });
  triggerDownload(blob, `${slug(form.name)}.csv`);
}

export async function exportFormulaToPdf(form: Formulation): Promise<void> {
  const [{ default: jsPDF }, { default: autoTable }] = await Promise.all([
    import("jspdf"),
    import("jspdf-autotable"),
  ]);
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const margin = 40;
  let y = margin;

  doc.setFontSize(16);
  doc.setTextColor(20, 30, 48);
  doc.text("FormuMind — Formulation Report", margin, y);
  y += 22;

  doc.setFontSize(11);
  doc.setTextColor(60, 60, 60);
  doc.text(form.name, margin, y);
  y += 16;
  doc.setFontSize(9);
  doc.setTextColor(120, 120, 120);
  doc.text(`Domain: ${form.domain}${form.score != null ? `   ·   Score: ${form.score.toFixed(3)}` : ""}`, margin, y);
  y += 18;

  autoTable(doc, {
    startY: y,
    head: [["Ingredient", "Role", "Formula", "Weight %"]],
    body: form.ingredients.map((i) => [i.name, i.role, i.formula ?? "—", `${i.weight_pct}`]),
    styles: { fontSize: 9, cellPadding: 4 },
    headStyles: { fillColor: [56, 189, 248], textColor: 20 },
    margin: { left: margin, right: margin },
  });

  // @ts-expect-error lastAutoTable is attached by the autotable plugin at runtime.
  y = (doc.lastAutoTable?.finalY ?? y) + 24;

  const predRows = Object.entries(form.predicted).map(([k, v]) => {
    const std = form.predicted_std?.[k];
    return [k, `${v}`, std != null ? `± ${std}` : "—"];
  });
  autoTable(doc, {
    startY: y,
    head: [["Predicted property", "Value", "Uncertainty"]],
    body: predRows,
    styles: { fontSize: 9, cellPadding: 4 },
    headStyles: { fillColor: [34, 211, 238], textColor: 20 },
    margin: { left: margin, right: margin },
  });

  if (form.rationale) {
    // @ts-expect-error lastAutoTable injected at runtime.
    y = (doc.lastAutoTable?.finalY ?? y) + 20;
    doc.setFontSize(9);
    doc.setTextColor(90, 90, 90);
    const wrapped = doc.splitTextToSize(`Rationale: ${form.rationale}`, 515);
    doc.text(wrapped, margin, y);
  }

  doc.save(`${slug(form.name)}.pdf`);
}
