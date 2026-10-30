/**
 * Bridge to the native desktop shell (desktop/src-tauri). Everything here is a no-op in a browser, so the Docker
 * install is unaffected. Tauri injects `__TAURI_INTERNALS__` into pages it loads, including this one, which the
 * shell serves from the local backend on 127.0.0.1 (granted in desktop/src-tauri/capabilities/default.json).
 */
type TauriInternals = { invoke: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T> };

function internals(): TauriInternals | undefined {
  return (window as unknown as { __TAURI_INTERNALS__?: TauriInternals }).__TAURI_INTERNALS__;
}

export const inDesktopShell = () => internals() !== undefined;

export async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const t = internals();
  if (!t) throw new Error("Only available in the desktop app");
  return t.invoke<T>(cmd, args);
}

export type AppInfo = {
  version: string;
  platform: string;
  arch: string;
  data_dir: string;
  log_dir: string;
  workbench_url: string | null;
  updater: boolean;
};

export type Sidecar = { name: string; binary: string; description: string; phase: number; enabled: boolean; status: string };

export const appInfo = () => invoke<AppInfo>("app_info");
export const listSidecars = () => invoke<Sidecar[]>("list_sidecars");
export const openDataDir = () => invoke<void>("open_data_dir");
export const openLogs = () => invoke<void>("open_logs");

/** Native approve / deny prompt. Outside the desktop app it falls back to the browser's confirm(). */
export async function requestApproval(title: string, detail: string, risk: "low" | "medium" | "high" = "medium"): Promise<boolean> {
  if (!inDesktopShell()) return window.confirm(`${title}\n\n${detail}`);
  return invoke<boolean>("request_approval", { request: { title, detail, risk, source: "workbench" } });
}
