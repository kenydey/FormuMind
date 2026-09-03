/** Verify critical frontend dependencies before dev/build. */
import { createRequire } from "module";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const req = createRequire(join(frontendRoot, "package.json"));

const REQUIRED = ["ag-grid-react", "ag-grid-community", "immer"];
const missing = REQUIRED.filter((name) => {
  try {
    req.resolve(name);
    return false;
  } catch {
    return true;
  }
});

if (missing.length > 0) {
  console.error("\n[FormuMind] 缺少前端依赖:", missing.join(", "));
  console.error("请在 frontend 目录执行: npm ci   （或 npm install）\n");
  process.exit(1);
}

console.log("[FormuMind] 前端依赖检查通过");
