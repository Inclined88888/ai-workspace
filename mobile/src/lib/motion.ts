/**
 * The phone's motion tokens, matching the desktop's (workbench/frontend/src/motion/tokens.css): the same three
 * springs, durations and per-theme personality. Pure, so it is unit tested; the Reanimated components that
 * use it live in components/motion.tsx.
 */
import type { ThemeId } from './theme';

export type MotionPref = 'full' | 'calm' | 'off';
export type Spring = { damping: number; stiffness: number; mass: number };
export type MotionTokens = {
  level: MotionPref;
  /** fast / base / slow / loop durations in ms */
  d: { fast: number; base: number; slow: number; loop: number };
  /** soft glide (cursor, orb), settle (cards, ticks; a little overshoot), snappy (buttons, nodes) */
  spring: { gentle: Spring; settle: Spring; snappy: Spring };
  /** how far things rise in, in points (0 in Calm) */
  travel: number;
};

const gentle: Spring = { damping: 26, stiffness: 170, mass: 1 };
const settle: Spring = { damping: 24, stiffness: 320, mass: 1 };
const snappy: Spring = { damping: 38, stiffness: 400, mass: 1 };
// Critically damped: arrives without any bounce. Used by Calm and High contrast.
const still = (s: Spring): Spring => ({ ...s, damping: 2 * Math.sqrt(s.stiffness * s.mass) });

export function motionTokens(theme: ThemeId, pref: MotionPref, osReduced = false): MotionTokens {
  const level: MotionPref = osReduced ? 'off' : pref;
  let t: MotionTokens = { level, d: { fast: 120, base: 180, slow: 260, loop: 1800 }, spring: { gentle, settle, snappy }, travel: 6 };
  if (theme === 'cherry') t = { ...t, d: { ...t.d, slow: 220 }, spring: { ...t.spring, settle: snappy } };
  if (theme === 'neon') t = { ...t, d: { fast: 100, base: 160, slow: 220, loop: 1400 } };
  if (theme === 'paper') t = { ...t, d: { ...t.d, loop: 2200 } };
  if (theme === 'contrast') t = { ...t, spring: { gentle: still(gentle), settle: still(settle), snappy: still(snappy) } };
  if (level === 'calm') t = { ...t, travel: 0, d: { ...t.d, loop: t.d.loop * 1.5 }, spring: { gentle: still(gentle), settle: still(settle), snappy: still(snappy) } };
  if (level === 'off') t = { ...t, travel: 0, d: { fast: 0, base: 0, slow: 0, loop: 0 } };
  return t;
}

/** expo-speech-recognition reports input volume from -2 (silence) to 10 (loud); the orb wants 0…1, eased. */
export function volumeLevel(v: number | null | undefined): number {
  if (v == null || !Number.isFinite(v)) return 0;
  const x = Math.min(1, Math.max(0, (v + 2) / 12));
  return Math.sqrt(x); // quiet speech still visibly moves the orb
}
