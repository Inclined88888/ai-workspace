import { useEffect, useState } from "react";

/**
 * Appearance: theme, type, size, density, motion and accent. One object in localStorage, applied to <html> as data
 * attributes and CSS variables, so every page restyles live without a reload. Six curated themes: Sunshine (the
 * default), Paper, Cherry & Ink, Neon, Dark and High contrast, plus "Match my system" (Sunshine or Dark from the OS).
 * The choice is also saved to the backend so the desktop window, Rooks Code, Rooks Browser and the phone follow it.
 * Anyone who already saved their own choices keeps them (Linen and Gallery still work, they are just not offered).
 */
export type ThemeId = "sunshine" | "paper" | "cherry" | "neon" | "night" | "contrast" | "auto" | "linen" | "gallery";
export type FontId = "editorial" | "modern" | "classic" | "system";
export type MotionId = "full" | "subtle" | "off";
export type AccentId = "theme" | "sun" | "cherry" | "ink" | "cobalt" | "forest" | "ochre";
export type Appearance = { theme: ThemeId; font: FontId; size: number; density: "comfortable" | "compact"; motion: MotionId; accent: AccentId };

/** "theme" accent = each theme's own designed accent (Sunshine's sun, Cherry's red, Neon's lime...). */
export const DEFAULTS: Appearance = { theme: "sunshine", font: "modern", size: 14, density: "comfortable", motion: "full", accent: "theme" };
const KEY = "wb-appearance";
const LEGACY = "wb-theme";

export type ThemeInfo = { id: ThemeId; label: string; note: string; mode: "light" | "dark" | "auto";
  /** paper, panel, text, muted, accent, on-accent: used to draw the mini preview on picker cards */
  swatch: { paper: string; panel: string; text: string; muted: string; accent: string; onAccent: string; border: string } };

/** The curated six, plus "Match my system". Linen and Gallery stay valid for anyone who saved them, just not offered. */
export const THEMES: ThemeInfo[] = [
  { id: "sunshine", label: "Sunshine", note: "Bright, warm and airy. The default.", mode: "light",
    swatch: { paper: "#fffdf8", panel: "#ffffff", text: "#2a2620", muted: "#615a4e", accent: "#ffc83d", onAccent: "#2a2000", border: "#f0e9da" } },
  { id: "paper", label: "Paper", note: "Soft newsprint, quiet ink.", mode: "light",
    swatch: { paper: "#f5f1e8", panel: "#fbf9f4", text: "#141311", muted: "#5a554c", accent: "#141311", onAccent: "#f5f1e8", border: "#dcd5c6" } },
  { id: "cherry", label: "Cherry & Ink", note: "Crisp white, black ink, one red.", mode: "light",
    swatch: { paper: "#fbf8f3", panel: "#ffffff", text: "#141311", muted: "#57524a", accent: "#c8102e", onAccent: "#ffffff", border: "#e2dace" } },
  { id: "neon", label: "Neon", note: "Ink black with one electric lime.", mode: "dark",
    swatch: { paper: "#0b0d10", panel: "#12151a", text: "#e9eef2", muted: "#a3adb8", accent: "#d4ff3a", onAccent: "#0b0d10", border: "#262b33" } },
  { id: "night", label: "Dark", note: "Calm ink, bone type. Easy at night.", mode: "dark",
    swatch: { paper: "#121110", panel: "#191816", text: "#efeae0", muted: "#aca598", accent: "#ff4d5e", onAccent: "#121110", border: "#33302b" } },
  { id: "contrast", label: "High contrast", note: "Pure black on white, bold edges.", mode: "light",
    swatch: { paper: "#ffffff", panel: "#ffffff", text: "#000000", muted: "#1f1f1f", accent: "#0033aa", onAccent: "#ffffff", border: "#000000" } },
  { id: "auto", label: "Match my system", note: "Sunshine by day, Dark when your computer is dark.", mode: "auto",
    swatch: { paper: "#fffdf8", panel: "#121110", text: "#2a2620", muted: "#aca598", accent: "#ffc83d", onAccent: "#2a2000", border: "#f0e9da" } },
];
const HIDDEN_THEMES: ThemeId[] = ["linen", "gallery"];
/** Themes with a hand-tuned palette whose accent never changes (an accent picked elsewhere would break contrast). */
const FIXED_ACCENT: ThemeId[] = ["neon", "contrast"];
export const DARK_THEMES: ThemeId[] = ["night", "neon"];

/** "auto" resolves to Sunshine or Dark from the OS setting. */
export function resolveTheme(t: ThemeId): Exclude<ThemeId, "auto"> {
  if (t !== "auto") return t;
  const dark = typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return dark ? "night" : "sunshine";
}
export const isDark = (t: ThemeId) => DARK_THEMES.includes(resolveTheme(t));

export const FONTS: { id: FontId; label: string; note: string; display: string; body: string }[] = [
  { id: "editorial", label: "Editorial", note: "Bodoni Moda + Hanken Grotesk", display: '"Bodoni Moda Variable", Didot, serif', body: '"Hanken Grotesk Variable", Helvetica, sans-serif' },
  { id: "modern", label: "Clean", note: "Hanken Grotesk throughout, the default", display: '"Hanken Grotesk Variable", Helvetica, sans-serif', body: '"Hanken Grotesk Variable", Helvetica, sans-serif' },
  { id: "classic", label: "Classic", note: "Bodoni Moda + a book serif", display: '"Bodoni Moda Variable", Didot, serif', body: 'Charter, "Iowan Old Style", Georgia, serif' },
  { id: "system", label: "System", note: "Your computer's own fonts", display: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif', body: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif' },
];
export const ACCENTS: { id: AccentId; label: string; color: string }[] = [
  { id: "theme", label: "Theme's own", color: "conic-gradient(#ffc83d 0 25%, #c8102e 0 50%, #d4ff3a 0 75%, #141311 0)" },
  { id: "sun", label: "Sun", color: "#ffc83d" },
  { id: "cherry", label: "Cherry", color: "#d21f3c" },
  { id: "ink", label: "Ink", color: "#141311" },
  { id: "cobalt", label: "Cobalt", color: "#2343b8" },
  { id: "forest", label: "Forest", color: "#2d6236" },
  { id: "ochre", label: "Ochre", color: "#a8660a" },
];
export const SIZES = { min: 12, max: 18 };

// Base sizes at 14px; every text token scales with the slider.
const TEXT_TOKENS: [string, number][] = [["--text-2xs", 10.5], ["--text-xs", 12], ["--text-sm", 13], ["--text-md", 14], ["--text-lg", 16],
  ["--text-xl", 20], ["--text-2xl", 28], ["--text-3xl", 40], ["--text-4xl", 56]];

export function readAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return sanitize(JSON.parse(raw));
    if (localStorage.getItem(LEGACY) === "dark") return { ...DEFAULTS, theme: "night" };
  } catch {
    /* storage unavailable or corrupt */
  }
  return DEFAULTS;
}

function sanitize(v: Partial<Appearance>): Appearance {
  const pick = <T extends string>(x: unknown, ok: readonly T[], d: T): T => (ok.includes(x as T) ? (x as T) : d);
  const size = Number(v.size);
  return {
    theme: pick(v.theme, [...THEMES.map((t) => t.id), ...HIDDEN_THEMES], DEFAULTS.theme),
    font: pick(v.font, FONTS.map((f) => f.id), DEFAULTS.font),
    size: Number.isFinite(size) ? Math.min(SIZES.max, Math.max(SIZES.min, Math.round(size))) : DEFAULTS.size,
    density: v.density === "compact" ? "compact" : "comfortable",
    motion: pick(v.motion, ["full", "subtle", "off"] as const, DEFAULTS.motion),
    accent: pick(v.accent, ACCENTS.map((a) => a.id), DEFAULTS.accent),
  };
}

/** Apply to a root element (the document, or a preview box). */
export function applyTo(el: HTMLElement, a: Appearance) {
  const theme = resolveTheme(a.theme);
  el.dataset.theme = theme === "night" ? "dark" : theme; // Night keeps the original [data-theme="dark"] tokens
  el.dataset.mode = DARK_THEMES.includes(theme) ? "dark" : "light";
  el.dataset.font = a.font;
  el.dataset.density = a.density;
  el.dataset.motion = a.motion;
  el.dataset.accent = FIXED_ACCENT.includes(theme) ? "theme" : a.accent;
  const scale = a.size / 14;
  for (const [name, base] of TEXT_TOKENS) {
    if (scale === 1) el.style.removeProperty(name);
    else el.style.setProperty(name, `${Math.round(base * scale * 10) / 10}px`);
  }
}

export function setAppearance(next: Partial<Appearance>) {
  const a = sanitize({ ...readAppearance(), ...next });
  applyTo(document.documentElement, a);
  try {
    localStorage.setItem(KEY, JSON.stringify(a));
    localStorage.removeItem(LEGACY);
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new CustomEvent("wb:appearance", { detail: a }));
  for (const fn of listeners) fn(a);
}

/** Other surfaces (the desktop window chrome, the backend copy that Rooks and the phone read) subscribe here. */
const listeners = new Set<(a: Appearance) => void>();
export function onAppearance(fn: (a: Appearance) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Choosing a theme also returns the accent to the theme's own, so every palette shows as designed. */
export function pickTheme(theme: ThemeId) {
  setAppearance({ theme, accent: "theme" });
}

/** Keep "Match my system" live when the OS flips between light and dark. */
export function followSystem(): () => void {
  const mq = typeof window !== "undefined" ? window.matchMedia?.("(prefers-color-scheme: dark)") : undefined;
  if (!mq) return () => undefined;
  const on = () => { const a = readAppearance(); if (a.theme === "auto") setAppearance({}); };
  mq.addEventListener?.("change", on);
  return () => mq.removeEventListener?.("change", on);
}

export function useAppearance(): [Appearance, (p: Partial<Appearance>) => void] {
  const [a, setA] = useState<Appearance>(readAppearance);
  useEffect(() => {
    const on = (e: Event) => setA((e as CustomEvent<Appearance>).detail);
    window.addEventListener("wb:appearance", on);
    return () => window.removeEventListener("wb:appearance", on);
  }, []);
  return [a, setAppearance];
}

/** True when nothing should move: the user chose Off, or the OS asks for reduced motion. */
export function motionOff(): boolean {
  if (typeof window === "undefined") return true;
  return document.documentElement.dataset.motion === "off" || !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}
