import { DARK_THEMES, palettes, resolveTheme, themeLabels } from '../theme';

function lum(hex: string) {
  const ch = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const n = (i: number) => parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16);
  return 0.2126 * ch(n(0)) + 0.7152 * ch(n(1)) + 0.0722 * ch(n(2));
}
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

describe('themes', () => {
  it('has the same six as the computer', () => {
    expect(themeLabels.map(([id]) => id)).toEqual(['sunshine', 'paper', 'cherry', 'neon', 'night', 'contrast']);
    expect(DARK_THEMES.sort()).toEqual(['neon', 'night']);
  });

  it('meets AA for every text pair', () => {
    for (const [id, p] of Object.entries(palettes)) {
      for (const bg of [p.paper, p.panel]) {
        for (const fg of [p.text, p.muted, p.faint, p.accentText]) expect([id, fg, bg, ratio(fg, bg) >= 4.5]).toEqual([id, fg, bg, true]);
      }
      expect([id, ratio(p.onAccent, p.accent) >= 4.5]).toEqual([id, true]);
      expect([id, ratio(p.onInk, p.ink) >= 4.5]).toEqual([id, true]);
    }
  });

  it('follows the computer, the phone, or its own pick', () => {
    expect(resolveTheme('computer', 'light', 'neon')).toBe('neon');
    expect(resolveTheme('computer', 'light', undefined)).toBe('sunshine');
    expect(resolveTheme('computer', 'dark', 'auto')).toBe('night');
    expect(resolveTheme('computer', 'light', 'gallery')).toBe('paper');
    expect(resolveTheme('computer', 'light', '<bad>')).toBe('sunshine');
    expect(resolveTheme('system', 'dark', 'neon')).toBe('night');
    expect(resolveTheme('system', 'light', 'neon')).toBe('sunshine');
    expect(resolveTheme('cherry', 'dark', 'neon')).toBe('cherry');
    expect(resolveTheme('light', null)).toBe('paper');
    expect(resolveTheme('dark', null)).toBe('night');
  });
});
