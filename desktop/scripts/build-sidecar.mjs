// Freeze the Workbench backend with PyInstaller and place it where Tauri expects external binaries:
//   desktop/src-tauri/binaries/workbench-server-<rust target triple>[.exe]
// Needs: Python 3.12 with desktop/sidecar/requirements.txt installed, and workbench/frontend/dist built.
// Usage: node scripts/build-sidecar.mjs [--target <triple>] [--python <python executable>]
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, chmodSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const desktop = join(here, "..");
const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };

const python = opt("--python") ?? process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const target = opt("--target") ?? process.env.TAURI_TARGET_TRIPLE ??
  /host: (\S+)/.exec(execFileSync("rustc", ["-vV"], { encoding: "utf8" }))[1];
const ext = target.includes("windows") ? ".exe" : "";

if (!existsSync(join(desktop, "..", "workbench", "frontend", "dist", "index.html"))) {
  console.error("workbench/frontend/dist is missing. Run: npm run frontend");
  process.exit(1);
}
const sidecar = join(desktop, "sidecar");
const dist = join(sidecar, "dist");
execFileSync(python, ["-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", dist,
  "--workpath", join(sidecar, "build"), join(sidecar, "workbench-server.spec")], { stdio: "inherit", cwd: sidecar });

const out = join(desktop, "src-tauri", "binaries");
mkdirSync(out, { recursive: true });
const dest = join(out, `workbench-server-${target}${ext}`);
copyFileSync(join(dist, `workbench-server${ext}`), dest);
if (!ext) chmodSync(dest, 0o755);
console.log(`sidecar ready: ${dest}`);
