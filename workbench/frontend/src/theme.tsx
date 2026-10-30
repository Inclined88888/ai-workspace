import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export type ThemePref = "system" | "light" | "dark";
const KEY = "wb-theme";

export function readTheme(): ThemePref {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

/** "system" removes the override so the CSS follows prefers-color-scheme live. */
export function applyTheme(pref: ThemePref) {
  const root = document.documentElement;
  if (pref === "system") delete root.dataset.theme;
  else root.dataset.theme = pref;
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
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

const OPTIONS: { value: ThemePref; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "Match system", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
];

export function ThemeSwitch() {
  const [pref, set] = useTheme();
  const move = (dir: number) => {
    const i = OPTIONS.findIndex((o) => o.value === pref);
    const next = OPTIONS[(i + dir + OPTIONS.length) % OPTIONS.length];
    set(next.value);
    document.getElementById(`theme-${next.value}`)?.focus();
  };
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Theme"
      onKeyDown={(e) => { if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); move(1); } if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); move(-1); } }}>
      {OPTIONS.map(({ value, label, Icon }) => (
        <button key={value} id={`theme-${value}`} type="button" role="radio" aria-checked={pref === value} aria-label={label} title={label}
          tabIndex={pref === value ? 0 : -1} onClick={() => set(value)}>
          <Icon size={14} />
        </button>
      ))}
    </div>
  );
}
