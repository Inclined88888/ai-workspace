/**
 * Editorial design tokens, matched to workbench/frontend/src/styles.css.
 * Ink on warm paper, a Didone display face, hairline rules, square corners, flat surfaces.
 * Six themes, the same as the computer: Sunshine (default), Paper, Cherry & Ink, Neon, Dark and High contrast.
 * The phone follows the computer's choice when paired ("Same as computer"), or its own pick in Settings.
 */
import { Platform } from 'react-native';

export type Palette = {
  paper: string;
  panel: string;
  panel2: string;
  panel3: string;
  border: string;
  borderStrong: string;
  rule: string;
  text: string;
  muted: string;
  faint: string;
  accent: string;
  accentText: string;
  accentSoft: string;
  onAccent: string;
  blueSoft: string;
  ink: string;
  onInk: string;
  ok: string;
  okSoft: string;
  warn: string;
  warnSoft: string;
  danger: string;
  dangerSoft: string;
  codeBg: string;
  scrim: string;
};

export const light: Palette = {
  paper: '#F5F1E8',
  panel: '#FBF9F4',
  panel2: '#EEE9DE',
  panel3: '#E6E0D2',
  border: '#DCD5C6',
  borderStrong: '#8C8474',
  rule: '#141311',
  text: '#141311',
  muted: '#5A554C',
  faint: '#655F54',
  accent: '#D21F3C',
  accentText: '#B5122B',
  accentSoft: '#F6E1DF',
  onAccent: '#FFFFFF',
  blueSoft: '#D5E5F4',
  ink: '#141311',
  onInk: '#F5F1E8',
  ok: '#2D6236',
  okSoft: '#E3EBDD',
  warn: '#7A4C00',
  warnSoft: '#F3E7CF',
  danger: '#A61C1C',
  dangerSoft: '#F6E1DF',
  codeBg: '#EEE9DE',
  scrim: 'rgba(20, 19, 17, 0.22)',
};

export const dark: Palette = {
  paper: '#121110',
  panel: '#191816',
  panel2: '#211F1C',
  panel3: '#2A2824',
  border: '#33302B',
  borderStrong: '#6F695F',
  rule: '#EFEAE0',
  text: '#EFEAE0',
  muted: '#ACA598',
  faint: '#99927F',
  accent: '#FF4D5E',
  accentText: '#FF6B78',
  accentSoft: '#3A1A1D',
  onAccent: '#121110',
  blueSoft: '#1E2A36',
  ink: '#EFEAE0',
  onInk: '#121110',
  ok: '#8FC79A',
  okSoft: '#1D2A1F',
  warn: '#E0B460',
  warnSoft: '#2E2617',
  danger: '#FF6B78',
  dangerSoft: '#3A1A1D',
  codeBg: '#211F1C',
  scrim: 'rgba(0, 0, 0, 0.5)',
};

const sunshine: Palette = {
  paper: '#FFFDF8', panel: '#FFFFFF', panel2: '#FAF5EA', panel3: '#F3ECDC', border: '#F0E9DA', borderStrong: '#D9CFBA', rule: '#2A2620',
  text: '#2A2620', muted: '#615A4E', faint: '#746C5F', accent: '#FFC83D', accentText: '#8A5A00', accentSoft: '#FFF4D1', onAccent: '#2A2000',
  blueSoft: '#F6F0E3', ink: '#2A2620', onInk: '#FFFDF8', ok: '#2F6B3A', okSoft: '#E8F3E9', warn: '#8A5A00', warnSoft: '#FFF4D1',
  danger: '#B42318', dangerSoft: '#FDECEA', codeBg: '#FAF6EE', scrim: 'rgba(42, 38, 32, 0.18)',
};
const paper: Palette = { ...light, accent: '#141311', accentText: '#141311', accentSoft: '#E6E0D2', onAccent: '#F5F1E8' };
const cherry: Palette = {
  ...light, paper: '#FBF8F3', panel: '#FFFFFF', panel2: '#F3EEE6', panel3: '#EBE4D8', border: '#E2DACE', borderStrong: '#8A8274',
  muted: '#57524A', faint: '#635D53', accent: '#C8102E', accentText: '#A50D26', accentSoft: '#FBE3E3', onAccent: '#FFFFFF',
  onInk: '#FBF8F3', danger: '#A50D26', dangerSoft: '#FBE3E3', codeBg: '#F3EEE6',
};
const neon: Palette = {
  paper: '#0B0D10', panel: '#12151A', panel2: '#181C22', panel3: '#20252C', border: '#262B33', borderStrong: '#5D6672', rule: '#E9EEF2',
  text: '#E9EEF2', muted: '#A3ADB8', faint: '#8F99A4', accent: '#D4FF3A', accentText: '#D4FF3A', accentSoft: '#1D2410', onAccent: '#0B0D10',
  blueSoft: '#10202A', ink: '#E9EEF2', onInk: '#0B0D10', ok: '#5EF0A0', okSoft: '#0F2419', warn: '#FFC94D', warnSoft: '#2A2210',
  danger: '#FF7A86', dangerSoft: '#2E1418', codeBg: '#07080A', scrim: 'rgba(0, 0, 0, 0.6)',
};
const contrast: Palette = {
  paper: '#FFFFFF', panel: '#FFFFFF', panel2: '#F2F2F2', panel3: '#E6E6E6', border: '#000000', borderStrong: '#000000', rule: '#000000',
  text: '#000000', muted: '#1F1F1F', faint: '#333333', accent: '#0033AA', accentText: '#0033AA', accentSoft: '#E0E8FF', onAccent: '#FFFFFF',
  blueSoft: '#E0E8FF', ink: '#000000', onInk: '#FFFFFF', ok: '#005C1F', okSoft: '#E1F5E7', warn: '#6B4000', warnSoft: '#FFF1D6',
  danger: '#A00000', dangerSoft: '#FFE5E5', codeBg: '#F2F2F2', scrim: 'rgba(0, 0, 0, 0.45)',
};

export type ThemeId = 'sunshine' | 'paper' | 'cherry' | 'neon' | 'night' | 'contrast';
export const palettes: Record<ThemeId, Palette> = { sunshine, paper, cherry, neon, night: dark, contrast };
export const themeLabels: [ThemeId, string][] = [
  ['sunshine', 'Sunshine'], ['paper', 'Paper'], ['cherry', 'Cherry & Ink'], ['neon', 'Neon'], ['night', 'Dark'], ['contrast', 'High contrast'],
];
export const DARK_THEMES: ThemeId[] = ['neon', 'night'];
const isTheme = (t: unknown): t is ThemeId => typeof t === 'string' && t in palettes;

/** Which theme to draw: the phone's own pick, the phone's light/dark setting, or the paired computer's choice. */
export function resolveTheme(pref: string, system: 'light' | 'dark' | null | undefined, desktop?: string | null): ThemeId {
  if (pref === 'light') return 'paper';
  if (pref === 'dark') return 'night';
  if (pref === 'system') return system === 'dark' ? 'night' : 'sunshine';
  if (pref === 'computer') {
    if (desktop === 'auto') return system === 'dark' ? 'night' : 'sunshine';
    if (desktop === 'linen' || desktop === 'gallery') return 'paper';
    return isTheme(desktop) ? desktop : 'sunshine';
  }
  return isTheme(pref) ? pref : 'sunshine';
}

/** Font family names registered in the root layout with expo-font. */
export const fonts = {
  display: 'BodoniModa_500Medium',
  displayItalic: 'BodoniModa_400Regular_Italic',
  displayRegular: 'BodoniModa_400Regular',
  ui: 'HankenGrotesk_400Regular',
  uiMedium: 'HankenGrotesk_500Medium',
  uiSemi: 'HankenGrotesk_600SemiBold',
  mono: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, Menlo, Consolas, monospace' }) as string,
} as const;

export const type = {
  label: { fontFamily: fonts.uiSemi, fontSize: 10.5, letterSpacing: 1.5, textTransform: 'uppercase' as const },
  small: { fontFamily: fonts.ui, fontSize: 12 },
  body: { fontFamily: fonts.ui, fontSize: 15, lineHeight: 22 },
  bodyMedium: { fontFamily: fonts.uiMedium, fontSize: 15, lineHeight: 22 },
  title: { fontFamily: fonts.display, fontSize: 22, lineHeight: 28 },
  headline: { fontFamily: fonts.display, fontSize: 34, lineHeight: 38, letterSpacing: -0.4 },
  masthead: { fontFamily: fonts.displayRegular, fontSize: 15, letterSpacing: 3.2, textTransform: 'uppercase' as const },
};

export const space = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40, 12: 56 } as const;

/** Motion: fades and page turns, 120-240 ms, no springs. */
export const motion = { fast: 120, base: 180, slow: 240 } as const;

/** Hairline: the thinnest rule the screen can draw. */
export const hairline = 0.75;
