import { useEffect, useState } from "react";

/**
 * Appearance: theme, type, size, density, motion and accent. One object in localStorage, applied to <html> as data
 * attributes and CSS variables, so every page restyles live without a reload. Paper (light) is the default; Night is
 * an explicit opt-in and never follows the OS.
 */
export type ThemeId = "paper" | "linen" | "gallery" | "night";
export type FontId = "editorial" | "modern" | "classic" | "system";
export type MotionId = "full" | "subtle" | "off";
export type AccentId = "cherry" | "ink" | "cobalt" | "forest" | "ochre";
export type Appearance = { theme: ThemeId; font: FontId; size: number; density: "comfortable" | "compact"; motion: MotionId; accent: AccentId };

export const DEFAULTS: Appearance = { theme: "paper", font: "editorial", size: 14, density: "comfortable", motion: "full", accent: "cherry" };
const KEY = "wb-appearance";
const LEGACY = "wb-theme";

export const THEMES: { id: ThemeId; label: string; note: string; swatch: [string, string] }[] = [
  { id: "paper", label: "Paper", note: "Warm newsprint, the default", swatch: ["#f5f1e8", "#141311"] },
  { id: "linen", label: "Linen", note: "Deeper, softer cream", swatch: ["#ebe3d2", "#1d1a16"] },
  { id: "gallery", label: "Gallery", note: "Clean white walls", swatch: ["#ffffff", "#111111"] },
  { id: "night", label: "Night", note: "Ink paper, bone type", swatch: ["#121110", "#efeae0"] },
];
export const FONTS: { id: FontId; label: string; note: string; display: string; body: string }[] = [
  { id: "editorial", label: "Editorial", note: "Bodoni Moda + Hanken Grotesk", display: '"Bodoni Moda Variable", Didot, serif', body: '"Hanken Grotesk Variable", Helvetica, sans-serif' },
  { id: "modern", label: "Modern", note: "Hanken Grotesk throughout", display: '"Hanken Grotesk Variable", Helvetica, sans-serif', body: '"Hanken Grotesk Variable", Helvetica, sans-serif' },
  { id: "classic", label: "Classic", note: "Bodoni Moda + a book serif", display: '"Bodoni Moda Variable", Didot, serif', body: 'Charter, "Iowan Old Style", Georgia, serif' },
  { id: "system", label: "System", note: "Your computer's own fonts", display: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif', body: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif' },
];
export const ACCENTS: { id: AccentId; label: string; color: string }[] = [
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
    theme: pick(v.theme, THEMES.map((t) => t.id), DEFAULTS.theme),
    font: pick(v.font, FONTS.map((f) => f.id), DEFAULTS.font),
    size: Number.isFinite(size) ? Math.min(SIZES.max, Math.max(SIZES.min, Math.round(size))) : DEFAULTS.size,
    density: v.density === "compact" ? "compact" : "comfortable",
    motion: pick(v.motion, ["full", "subtle", "off"] as const, DEFAULTS.motion),
    accent: pick(v.accent, ACCENTS.map((a) => a.id), DEFAULTS.accent),
  };
}

/** Apply to a root element (the document, or a preview box). */
export function applyTo(el: HTMLElement, a: Appearance) {
  if (a.theme === "night") el.dataset.theme = "dark";
  else if (a.theme === "paper") delete el.dataset.theme;
  else el.dataset.theme = a.theme;
  el.dataset.font = a.font;
  el.dataset.density = a.density;
  el.dataset.motion = a.motion;
  el.dataset.accent = a.accent;
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
