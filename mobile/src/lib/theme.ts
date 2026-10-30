/**
 * Editorial design tokens, matched to workbench/frontend/src/styles.css.
 * Ink on warm paper, a Didone display face, hairline rules, square corners, flat surfaces.
 * One accent (cherry red) and one secondary (baby blue, fills only). Light is the default; dark is opt-in.
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
