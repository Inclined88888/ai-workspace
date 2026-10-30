import { useEffect, useState } from "react";

/** Light is always the default. Dark is an explicit opt-in and never follows the OS. */
export type ThemePref = "light" | "dark";
const KEY = "wb-theme";

export function readTheme(): ThemePref {
  try {
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(pref: ThemePref) {
  const root = document.documentElement;
  if (pref === "dark") root.dataset.theme = "dark";
  else delete root.dataset.theme;
  try {
    if (pref === "dark") localStorage.setItem(KEY, "dark");
    else localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new CustomEvent("wb:theme", { detail: pref }));
}

export function useTheme(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(readTheme);
  useEffect(() => {
    const on = (e: Event) => setPref((e as CustomEvent<ThemePref>).detail);
    window.addEventListener("wb:theme", on);
    return () => window.removeEventListener("wb:theme", on);
  }, []);
  return [pref, applyTheme];
}

const OPTIONS: { value: ThemePref; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

/** Two-way text toggle (radiogroup: arrow keys move, Tab leaves). */
export function ThemeSwitch() {
  const [pref, set] = useTheme();
  const move = () => {
    const next = pref === "light" ? "dark" : "light";
    set(next);
    document.getElementById(`theme-${next}`)?.focus();
  };
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Theme"
      onKeyDown={(e) => { if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.key)) { e.preventDefault(); move(); } }}>
      {OPTIONS.map(({ value, label }) => (
        <button key={value} id={`theme-${value}`} type="button" role="radio" aria-checked={pref === value}
          tabIndex={pref === value ? 0 : -1} onClick={() => set(value)}>
          {label}
        </button>
      ))}
    </div>
  );
}
