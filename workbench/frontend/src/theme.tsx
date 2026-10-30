/** Back-compat shim: the old Light/Dark API now maps onto Appearance (Paper / Night). */
import { readAppearance, setAppearance, useAppearance } from "./appearance";

export type ThemePref = "light" | "dark";

export function readTheme(): ThemePref {
  return readAppearance().theme === "night" ? "dark" : "light";
}

export function applyTheme(pref: ThemePref) {
  const cur = readAppearance().theme;
  setAppearance({ theme: pref === "dark" ? "night" : cur === "night" ? "paper" : cur });
}

export function useTheme(): [ThemePref, (p: ThemePref) => void] {
  const [a] = useAppearance();
  return [a.theme === "night" ? "dark" : "light", applyTheme];
}
