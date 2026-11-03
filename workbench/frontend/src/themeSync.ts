import { api } from "./api";
import { type Appearance, followSystem, isDark, onAppearance, resolveTheme, THEMES } from "./appearance";
import { inDesktopShell, invoke } from "./desktop";

/**
 * Carries the chosen look beyond this page:
 *  - the backend keeps a copy (PUT /api/appearance) that the phone app reads when paired, and that Rooks Code and
 *    Rooks Browser read from ~/.ai-workspace/appearance.json for their terminal colours;
 *  - in the desktop app, the native window follows it: light or dark title bar, and the window background matches
 *    the theme's chrome so there is no white flash between pages.
 * Every call is best-effort: signed out, offline or in a plain browser, the page still restyles locally.
 */
let timer: ReturnType<typeof setTimeout> | undefined;

export function saveAppearanceRemote(a: Appearance) {
  clearTimeout(timer);
  timer = setTimeout(() => {
    api("/appearance", { method: "PUT", body: { ...a, resolved: resolveTheme(a.theme), dark: isDark(a.theme) } }).catch(() => undefined);
  }, 250);
}

export async function applyWindowChrome(a: Appearance) {
  if (!inDesktopShell()) return;
  const id = resolveTheme(a.theme);
  const bg = THEMES.find((t) => t.id === id)?.swatch.paper ?? "#fffdf8";
  const theme = isDark(a.theme) ? "dark" : "light";
  // Tauri 2 core window commands (granted in desktop/src-tauri/capabilities/default.json). Older shells ignore them.
  await invoke("plugin:window|set_theme", { label: "main", value: a.theme === "auto" ? null : theme }).catch(() => undefined);
  await invoke("plugin:window|set_background_color", { label: "main", value: bg }).catch(() => undefined);
}

/** Wire it up once at start-up. */
export function startThemeSync(initial: Appearance) {
  applyWindowChrome(initial);
  followSystem();
  onAppearance((a) => { applyWindowChrome(a); saveAppearanceRemote(a); });
}
