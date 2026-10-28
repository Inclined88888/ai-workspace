// Appearance: themes, fonts, text size, density, corners and background pattern.
// A theme is a few base colours; every other token is derived here and nudged until text meets WCAG AA contrast,
// so custom themes stay readable whatever colours are picked. Fonts are bundled (no requests to font services)
// and only downloaded by the browser when selected.
import "@fontsource-variable/inter";
import "@fontsource-variable/manrope";
import "@fontsource-variable/ibm-plex-sans";
import "@fontsource-variable/nunito";
import "@fontsource-variable/literata";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource/atkinson-hyperlegible/400.css";
import "@fontsource/atkinson-hyperlegible/700.css";
import "@fontsource/opendyslexic/400.css";
import "@fontsource/opendyslexic/700.css";

export type Palette = { mode: "light" | "dark"; bg: string; panel: string; text: string; accent: string };
export type Theme = Palette & { id: string; name: string; font?: string };
export type Appearance = {
  theme: string;            // a preset id, a custom theme id, or "system"
  font: string;
  size: number;             // base text size in px
  density: "compact" | "comfortable" | "spacious";
  corners: "square" | "soft" | "round";
  pattern: "none" | "dots" | "grid";
  accent: string;           // optional override, "" = theme accent
  custom: Theme[];
};

export const PRESETS: Theme[] = [
  { id: "rooks", name: "ROOKS", mode: "light", bg: "#faf9f6", panel: "#ffffff", text: "#1d1c1a", accent: "#3f4bd1" },
  { id: "paper", name: "Paper", mode: "light", bg: "#f5f0e6", panel: "#fffdf7", text: "#2a241d", accent: "#a8481f", font: "literata" },
  { id: "sage", name: "Sage", mode: "light", bg: "#eef2ec", panel: "#fafcf8", text: "#1d281e", accent: "#2f7445" },
  { id: "sky", name: "Sky", mode: "light", bg: "#edf3f9", panel: "#ffffff", text: "#132131", accent: "#1d68b0" },
  { id: "blossom", name: "Blossom", mode: "light", bg: "#fbf0f3", panel: "#fffafc", text: "#371d27", accent: "#b02e66" },
  { id: "contrast-light", name: "High contrast", mode: "light", bg: "#ffffff", panel: "#ffffff", text: "#000000", accent: "#0035c8" },
  { id: "rooks-dark", name: "ROOKS dark", mode: "dark", bg: "#141413", panel: "#1b1b1a", text: "#ecebe8", accent: "#5b63d6" },
  { id: "graphite", name: "Graphite", mode: "dark", bg: "#1a1b1e", panel: "#222327", text: "#e6e6e8", accent: "#7aa7f5" },
  { id: "midnight", name: "Midnight", mode: "dark", bg: "#0a0f1d", panel: "#111a2d", text: "#dce5f4", accent: "#6f8dff" },
  { id: "forest", name: "Forest", mode: "dark", bg: "#0e1611", panel: "#152019", text: "#dbeadf", accent: "#4fb37a" },
  { id: "ember", name: "Ember", mode: "dark", bg: "#191110", panel: "#231715", text: "#f2e5de", accent: "#e2794c" },
  { id: "plum", name: "Plum", mode: "dark", bg: "#16101b", panel: "#1f1727", text: "#ecdff2", accent: "#b47ce0" },
  { id: "terminal", name: "Terminal", mode: "dark", bg: "#090e0a", panel: "#0e1510", text: "#c6f0cf", accent: "#38c96f", font: "mono" },
  { id: "contrast-dark", name: "High contrast dark", mode: "dark", bg: "#000000", panel: "#0a0a0a", text: "#ffffff", accent: "#ffd400" },
];

export const FONTS: { id: string; name: string; stack: string; note?: string }[] = [
  { id: "system", name: "System", stack: 'ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif', note: "Your device's own font" },
  { id: "inter", name: "Inter", stack: '"Inter Variable", ui-sans-serif, system-ui, sans-serif' },
  { id: "manrope", name: "Manrope", stack: '"Manrope Variable", ui-sans-serif, system-ui, sans-serif' },
  { id: "plex", name: "IBM Plex Sans", stack: '"IBM Plex Sans Variable", ui-sans-serif, system-ui, sans-serif' },
  { id: "nunito", name: "Nunito", stack: '"Nunito Variable", ui-rounded, ui-sans-serif, system-ui, sans-serif', note: "Rounded and friendly" },
  { id: "literata", name: "Literata", stack: '"Literata Variable", Georgia, "Times New Roman", serif', note: "Serif, made for long reading" },
  { id: "mono", name: "JetBrains Mono", stack: '"JetBrains Mono Variable", ui-monospace, Menlo, Consolas, monospace' },
  { id: "hyperlegible", name: "Atkinson Hyperlegible", stack: '"Atkinson Hyperlegible", ui-sans-serif, system-ui, sans-serif', note: "Designed for low vision" },
  { id: "dyslexic", name: "OpenDyslexic", stack: '"OpenDyslexic", ui-sans-serif, system-ui, sans-serif', note: "Designed for dyslexia" },
];

export const DEFAULTS: Appearance = { theme: "system", font: "system", size: 14, density: "comfortable", corners: "soft", pattern: "none", accent: "", custom: [] };
const CACHE_KEY = "wb-appearance";

// Colour maths (sRGB, WCAG 2 relative luminance)
const rgb = (h: string) => { const n = parseInt(h.replace("#", "").padEnd(6, "0").slice(0, 6), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
const hex = (c: number[]) => "#" + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");
export const mix = (a: string, b: string, t: number) => { const x = rgb(a), y = rgb(b); return hex(x.map((v, i) => v + (y[i] - v) * t)); };
const lum = (h: string) => { const [r, g, b] = rgb(h).map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
export const contrast = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
/** Move `fg` toward `toward` until it reaches `min` contrast against every background. */
function ensure(fg: string, bgs: string[], min: number, toward: string): string {
  let c = fg;
  for (let i = 0; i <= 20 && bgs.some((b) => contrast(c, b) < min); i++) c = mix(fg, toward, i / 20);
  return c;
}
export const isHex = (s: unknown): s is string => typeof s === "string" && /^#[0-9a-fA-F]{6}$/.test(s);

/** All colour tokens for a palette. */
export function tokens(p: Palette, accentOverride = ""): Record<string, string> {
  // Judge light or dark from the actual panel colour, so a mislabelled custom theme still works
  const dark = contrast(p.panel, "#000000") < contrast(p.panel, "#ffffff");
  // Mid tones can't carry readable text in any colour: push surfaces toward light or dark until they can
  const firm = (c: string) => { const lighter = contrast(c, "#000000") >= contrast(c, "#ffffff"); return ensure(c, [lighter ? "#000000" : "#ffffff"], 9, lighter ? "#ffffff" : "#000000"); };
  const panel = firm(p.panel);
  const bg0 = firm(p.bg);
  // Body text first: everything else is corrected toward it, so it must itself be clearly readable
  const text0 = ensure(p.text, [panel], 7, dark ? "#ffffff" : "#000000");
  // Page and panels must share a polarity, since headings sit straight on the page: derive the page from the panel if not
  const bg = contrast(text0, bg0) >= 7 ? bg0 : mix(panel, text0, dark ? -0.04 : 0.03);
  const text = ensure(text0, [panel, bg], 7, dark ? "#ffffff" : "#000000");
  const panel2 = mix(panel, text, dark ? 0.06 : 0.035);
  const accent0 = isHex(accentOverride) ? accentOverride : p.accent;
  // The accent fill must carry its own label at AA and stand out from the panel at 3:1 (WCAG non-text contrast).
  // Search the closest colour to the chosen accent, darker or lighter, that meets both.
  const label = (c: string) => (contrast("#ffffff", c) >= contrast("#000000", c) ? "#ffffff" : "#000000");
  let accent = accent0;
  search: for (let i = 0; i <= 20; i++) {
    for (const toward of ["#000000", "#ffffff"]) {
      const c = mix(accent0, toward, i / 20);
      if (contrast(label(c), c) >= 4.5 && contrast(c, panel) >= 3) { accent = c; break search; }
    }
  }
  const onAccent = label(accent);
  const danger = dark ? "#f06a6a" : "#c62f2f";
  const ok = dark ? "#4cc47e" : "#1b7d44";
  const warn = dark ? "#f0b04c" : "#9a5c00";
  return {
    "--bg": bg, "--panel": panel, "--panel-2": panel2, "--text": text,
    "--border": mix(panel, text, dark ? 0.16 : 0.12), "--border-strong": mix(panel, text, dark ? 0.26 : 0.2),
    "--muted": ensure(mix(text, panel, 0.35), [panel, panel2, bg], 4.5, text),
    "--faint": ensure(mix(text, panel, 0.5), [panel, panel2, bg], 4.5, text),
    "--accent": accent, "--on-accent": onAccent,
    "--accent-soft": mix(panel, accent, dark ? 0.2 : 0.11),
    "--accent-text": ensure(accent0, [panel, panel2, bg, mix(panel, accent, dark ? 0.2 : 0.11)], 4.5, text),
    // status colours are read as text on panels and on their own tinted badges, so both must reach AA
    "--danger": ensure(danger, [panel, panel2, mix(panel, danger, dark ? 0.18 : 0.1)], 4.5, text), "--danger-soft": mix(panel, danger, dark ? 0.18 : 0.1),
    "--ok": ensure(ok, [panel, panel2, mix(panel, ok, dark ? 0.18 : 0.1)], 4.5, text), "--ok-soft": mix(panel, ok, dark ? 0.18 : 0.1),
    "--warn": ensure(warn, [panel, panel2, mix(panel, warn, dark ? 0.18 : 0.1)], 4.5, text), "--warn-soft": mix(panel, warn, dark ? 0.18 : 0.1),
  };
}

export function findTheme(a: Appearance, id = a.theme): Theme | null {
  if (id === "system") return null;
  return a.custom.find((t) => t.id === id) ?? PRESETS.find((t) => t.id === id) ?? null;
}

/** Apply to the document. "system" keeps the stylesheet's own light/dark colours, following the device setting. */
export function apply(a: Appearance): void {
  const root = document.documentElement;
  const t = findTheme(a);
  const colourKeys = Object.keys(tokens(PRESETS[0]));
  colourKeys.forEach((k) => root.style.removeProperty(k));
  if (t) {
    Object.entries(tokens(t, a.accent)).forEach(([k, v]) => root.style.setProperty(k, v));
    root.dataset.theme = t.mode;
    root.style.colorScheme = t.mode;
  } else {
    root.style.removeProperty("color-scheme");
    if (root.dataset.theme !== "light" && root.dataset.theme !== "dark") delete root.dataset.theme;
    if (isHex(a.accent)) {
      const dark = root.dataset.theme === "dark" || (!root.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
      const base = PRESETS.find((p) => p.id === (dark ? "rooks-dark" : "rooks"))!;
      const tk = tokens(base, a.accent);
      ["--accent", "--on-accent", "--accent-soft", "--accent-text"].forEach((k) => root.style.setProperty(k, tk[k]));
    }
  }
  const font = FONTS.find((f) => f.id === a.font) ?? FONTS[0];
  root.style.setProperty("--font", font.stack);
  root.style.setProperty("--font-size", `${Math.min(18, Math.max(12, a.size || 14))}px`);
  root.dataset.density = a.density;
  root.dataset.corners = a.corners;
  root.dataset.pattern = a.pattern;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", getComputedStyle(root).getPropertyValue("--accent").trim() || "#3f4bd1");
}

export function normalise(raw: unknown): Appearance {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<Appearance>;
  const custom = Array.isArray(r.custom) ? r.custom.filter((t) => t && isHex(t.bg) && isHex(t.panel) && isHex(t.text) && isHex(t.accent) && typeof t.id === "string")
    .slice(0, 12).map((t) => ({ ...t, name: String(t.name || "My theme").slice(0, 40), mode: t.mode === "dark" ? "dark" : "light" } as Theme)) : [];
  const a: Appearance = {
    theme: typeof r.theme === "string" ? r.theme : DEFAULTS.theme,
    font: FONTS.some((f) => f.id === r.font) ? r.font! : DEFAULTS.font,
    size: typeof r.size === "number" ? Math.min(18, Math.max(12, Math.round(r.size))) : DEFAULTS.size,
    density: r.density === "compact" || r.density === "spacious" ? r.density : "comfortable",
    corners: r.corners === "square" || r.corners === "round" ? r.corners : "soft",
    pattern: r.pattern === "dots" || r.pattern === "grid" ? r.pattern : "none",
    accent: isHex(r.accent) ? r.accent : "",
    custom,
  };
  if (a.theme !== "system" && !findTheme(a)) a.theme = "system";
  return a;
}

export function cached(): Appearance {
  try { return normalise(JSON.parse(localStorage.getItem(CACHE_KEY) || "null")); } catch { return DEFAULTS; }
}
export function cache(a: Appearance): void {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(a)); } catch { /* storage unavailable */ }
}
