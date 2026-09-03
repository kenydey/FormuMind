// Regression check for the lab-workbench AG Grid (v33+ modular architecture).
//
// Without `ModuleRegistry.registerModules([AllCommunityModule])` the grid
// silently loses editing (error #200: TextEditor/EditCore not registered) —
// exactly the "台账无法录入数据" failure. LabWorkbench.tsx must keep the
// registration + `theme="legacy"` (CSS themes) for editing to work.
//
// Usage:
//   node scripts/repro-workbench-grid.mjs           # broken baseline (no modules)
//   node scripts/repro-workbench-grid.mjs --fixed   # expected: edit mode works, no errors
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!DOCTYPE html><div id='root'></div>", { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.getComputedStyle = dom.window.getComputedStyle;
globalThis.MutationObserver = dom.window.MutationObserver;
globalThis.Node = dom.window.Node;
globalThis.Element = dom.window.Element;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
globalThis.HTMLSelectElement = dom.window.HTMLSelectElement;
globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
globalThis.HTMLButtonElement = dom.window.HTMLButtonElement;
globalThis.CustomEvent = dom.window.CustomEvent;
globalThis.KeyboardEvent = dom.window.KeyboardEvent;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const errors = [];
const origError = console.error;
console.error = (...a) => { errors.push(a.join(" ")); };

const ag = await import("ag-grid-community");

if (process.argv.includes("--fixed")) {
  ag.ModuleRegistry.registerModules([ag.AllCommunityModule]);
}

const div = dom.window.document.getElementById("root");
let gridApi = null;
try {
  gridApi = ag.createGrid(div, {
    theme: process.argv.includes("--fixed") ? "legacy" : undefined,
    columnDefs: [{ field: "id" }, { field: "status", editable: true }],
    rowData: [{ id: 1, status: "Pending" }],
  });
} catch (e) {
  errors.push(String(e));
}

await new Promise((r) => setTimeout(r, 300));

// The user-facing symptom: can a cell actually enter edit mode?
let editingCells = [];
try {
  gridApi?.startEditingCell({ rowIndex: 0, colKey: "status" });
  await new Promise((r) => setTimeout(r, 100));
  editingCells = gridApi?.getEditingCells() ?? [];
} catch (e) {
  errors.push(String(e));
}

console.error = origError;

const rendered = div.querySelectorAll(".ag-row").length;
console.log("grid api created:", !!gridApi);
console.log("rendered rows:", rendered);
console.log("cell enters edit mode:", editingCells.length > 0);
console.log("console errors:", errors.length ? errors.slice(0, 4).map(e => e.slice(0, 300)) : "none");
process.exit(0);
