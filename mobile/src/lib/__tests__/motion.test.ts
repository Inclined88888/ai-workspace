import { motionTokens, volumeLevel } from '../motion';

describe('motion tokens', () => {
  it('match the desktop springs and durations by default', () => {
    const t = motionTokens('sunshine', 'full');
    expect(t.spring.settle).toEqual({ damping: 24, stiffness: 320, mass: 1 });
    expect(t.d).toEqual({ fast: 120, base: 180, slow: 260, loop: 1800 });
    expect(t.travel).toBe(6);
  });
  it('give each theme its personality', () => {
    expect(motionTokens('neon', 'full').d.slow).toBeLessThan(motionTokens('sunshine', 'full').d.slow);
    expect(motionTokens('cherry', 'full').spring.settle).toEqual(motionTokens('cherry', 'full').spring.snappy);
    const hc = motionTokens('contrast', 'full').spring.settle;
    expect(hc.damping).toBeCloseTo(2 * Math.sqrt(hc.stiffness), 5); // no overshoot
  });
  it('Calm removes travel and bounce, Off and the OS setting remove motion', () => {
    const calm = motionTokens('sunshine', 'calm');
    expect(calm.travel).toBe(0);
    expect(calm.spring.gentle.damping).toBeCloseTo(2 * Math.sqrt(170), 5);
    expect(motionTokens('neon', 'off').d.slow).toBe(0);
    expect(motionTokens('sunshine', 'full', true).level).toBe('off');
  });
});

describe('voice level', () => {
  it('maps -2…10 onto 0…1 and ignores junk', () => {
    expect(volumeLevel(-2)).toBe(0);
    expect(volumeLevel(10)).toBe(1);
    expect(volumeLevel(4)).toBeGreaterThan(0.5);
    expect(volumeLevel(99)).toBe(1);
    expect(volumeLevel(undefined)).toBe(0);
    expect(volumeLevel(NaN)).toBe(0);
  });
});
