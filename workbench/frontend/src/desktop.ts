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

/** True when the user isn't looking at this page (another app or tab in front, or the window hidden to the tray). */
export const inBackground = () => document.hidden || !document.hasFocus();

/** Ask for browser notification permission. Call from a click or key press: browsers ignore it otherwise. */
export function askNotifyPermission() {
  if (inDesktopShell() || typeof Notification === "undefined") return;
  if (Notification.permission === "default") void Notification.requestPermission().catch(() => {});
}

/** OS notification: native in the desktop app, the Web Notification API in a browser (only once allowed).
 *  onClick runs when a browser notification is clicked; the desktop app brings its window up from the tray. */
export async function notify(title: string, body = "", opts: { tag?: string; onClick?: () => void } = {}): Promise<void> {
  if (inDesktopShell()) {
    try { await invoke<void>("notify", { title, body }); } catch { /* notifications switched off for the app */ }
    return;
  }
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const n = new Notification(title, { body, tag: opts.tag });
  n.onclick = () => { window.focus(); opts.onClick?.(); n.close(); };
}

/** Where an aiworkspace:// link or the tray menu sends the page (desktop/src-tauri/src/native.rs). */
export type DesktopLink = { path: string; draft?: string };
export const desktopReady = () => invoke<DesktopLink | null>("desktop_ready");

/** Native approve / deny prompt. Outside the desktop app it falls back to the browser's confirm(). */
export async function requestApproval(title: string, detail: string, risk: "low" | "medium" | "high" = "medium"): Promise<boolean> {
  if (!inDesktopShell()) return window.confirm(`${title}\n\n${detail}`);
  return invoke<boolean>("request_approval", { request: { title, detail, risk, source: "workbench" } });
}
