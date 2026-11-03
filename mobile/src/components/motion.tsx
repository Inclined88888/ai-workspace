/**
 * The phone's signature moments, on Reanimated (UI thread, transform and opacity only) with haptics where a
 * moment lands: thinking shimmer, spring-in cards and replies, the completion tick, the voice orb,
 * timeline nodes and skeletons. Everything reads motionTokens(theme, prefs.motion, OS reduce-motion).
 */
import React, { useEffect, useMemo } from 'react';
import { StyleProp, TextStyle, View, ViewStyle } from 'react-native';
import Animated, {
  cancelAnimation, Easing, SharedValue, useAnimatedStyle, useReducedMotion, useSharedValue, withDelay, withRepeat, withSequence, withSpring, withTiming,
} from 'react-native-reanimated';
import { useSession } from '@/lib/session';
import { motionTokens, MotionTokens } from '@/lib/motion';
import { fonts } from '@/lib/theme';
import { success } from '@/lib/haptics';

export function useMotion(): MotionTokens {
  const { theme, prefs } = useSession();
  const reduced = useReducedMotion();
  return useMemo(() => motionTokens(theme, prefs.motion ?? 'full', reduced), [theme, prefs.motion, reduced]);
}

/** "Thinking…" in italic display type, breathing slowly; cut the moment the answer starts. */
export function Thinking({ label, style }: { label: string; style?: StyleProp<TextStyle> }) {
  const m = useMotion();
  const { colors: c } = useSession();
  const o = useSharedValue(1);
  useEffect(() => {
    if (m.level === 'off') { o.value = 1; return; }
    o.value = withRepeat(withSequence(withTiming(0.42, { duration: m.d.loop / 2, easing: Easing.inOut(Easing.quad) }), withTiming(1, { duration: m.d.loop / 2, easing: Easing.inOut(Easing.quad) })), -1);
    return () => cancelAnimation(o);
  }, [m.level, m.d.loop, o]);
  const a = useAnimatedStyle(() => ({ opacity: o.value }));
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }} accessibilityRole="progressbar" accessibilityLabel={label}>
      <Dot color={c.accent} />
      <Animated.Text style={[{ fontFamily: fonts.displayItalic, fontSize: 16, color: c.muted }, style, a]}>{label}…</Animated.Text>
    </View>
  );
}

function Dot({ color }: { color: string }) {
  const m = useMotion();
  const s = useSharedValue(1);
  useEffect(() => {
    if (m.level !== 'full') { s.value = 1; return; }
    s.value = withRepeat(withSequence(withTiming(0.55, { duration: m.d.loop / 2 }), withTiming(1, { duration: m.d.loop / 2 })), -1);
    return () => cancelAnimation(s);
  }, [m.level, m.d.loop, s]);
  const a = useAnimatedStyle(() => ({ transform: [{ scale: s.value }] }));
  return <Animated.View style={[{ width: 6, height: 6, backgroundColor: color }, a]} />;
}

/** Rises in on the settle spring (cards, approvals). Calm fades only; Off appears. */
export function SpringIn({ children, delay = 0, style }: { children: React.ReactNode; delay?: number; style?: StyleProp<ViewStyle> }) {
  const m = useMotion();
  const p = useSharedValue(m.level === 'off' ? 1 : 0);
  useEffect(() => {
    if (m.level === 'off') return;
    p.value = withDelay(delay, m.level === 'calm' ? withTiming(1, { duration: m.d.slow }) : withSpring(1, m.spring.settle));
  }, [delay, m, p]);
  const travel = m.travel * 2;
  const a = useAnimatedStyle(() => ({ opacity: Math.min(1, p.value * 1.6), transform: [{ translateY: (1 - p.value) * travel }, { scale: 0.98 + 0.02 * p.value }] }));
  return <Animated.View style={[style, a]}>{children}</Animated.View>;
}

/** Ring settles in, the tick pops after it, and a success haptic lands with the tick. */
export function DoneTick({ size = 22, haptic = true }: { size?: number; haptic?: boolean }) {
  const m = useMotion();
  const { colors: c } = useSession();
  const ring = useSharedValue(m.level === 'off' ? 1 : 0);
  const tick = useSharedValue(m.level === 'off' ? 1 : 0);
  useEffect(() => {
    if (haptic) success();
    if (m.level === 'off') return;
    ring.value = m.level === 'calm' ? withTiming(1, { duration: m.d.slow }) : withSpring(1, m.spring.snappy);
    tick.value = withDelay(m.level === 'calm' ? 0 : 160, m.level === 'calm' ? withTiming(1, { duration: m.d.slow }) : withSpring(1, m.spring.settle));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when it lands
  }, []);
  const ra = useAnimatedStyle(() => ({ opacity: ring.value, transform: [{ scale: 0.6 + 0.4 * ring.value }] }));
  const ta = useAnimatedStyle(() => ({ opacity: tick.value, transform: [{ scale: tick.value }, { rotate: '-45deg' }] }));
  return (
    <Animated.View accessibilityLabel="Done" style={[{ width: size, height: size, borderRadius: size / 2, borderWidth: 1.5, borderColor: c.ok, alignItems: 'center', justifyContent: 'center' }, ra]}>
      <Animated.View style={[{ width: size * 0.46, height: size * 0.24, borderLeftWidth: 2, borderBottomWidth: 2, borderColor: c.ok, marginTop: -size * 0.08 }, ta]} />
    </Animated.View>
  );
}

/** A timeline node: springs in when a step starts, breathes while it runs, fills when it's done. */
export function Node({ tone }: { tone: 'ok' | 'bad' | 'quiet' }) {
  const m = useMotion();
  const { colors: c } = useSession();
  const s = useSharedValue(m.level === 'off' ? 1 : 0);
  const live = useSharedValue(1);
  useEffect(() => {
    if (m.level !== 'off') s.value = withSpring(1, m.spring.snappy);
  }, [m, s]);
  useEffect(() => {
    if (tone === 'quiet' && m.level === 'full') live.value = withRepeat(withSequence(withTiming(0.4, { duration: m.d.loop / 2 }), withTiming(1, { duration: m.d.loop / 2 })), -1);
    else { cancelAnimation(live); live.value = withTiming(1, { duration: m.d.fast }); }
    return () => cancelAnimation(live);
  }, [tone, m.level, m.d, live]);
  const a = useAnimatedStyle(() => ({ opacity: live.value, transform: [{ scale: s.value }] }));
  const color = tone === 'ok' ? c.ok : tone === 'bad' ? c.danger : c.accent;
  return <Animated.View style={[{ width: 6, height: 6, backgroundColor: color }, a]} />;
}

/** The dictation orb: breathes while listening and swells with your voice (level is 0…1 from volumeLevel). */
export function VoiceOrb({ on, level, size = 26 }: { on: boolean; level: SharedValue<number>; size?: number }) {
  const m = useMotion();
  const { colors: c } = useSession();
  const breathe = useSharedValue(0);
  useEffect(() => {
    if (on && m.level === 'full') breathe.value = withRepeat(withSequence(withTiming(1, { duration: 900 }), withTiming(0, { duration: 900 })), -1);
    else { cancelAnimation(breathe); breathe.value = 0; }
    return () => cancelAnimation(breathe);
  }, [on, m.level, breathe]);
  const gentle = m.spring.gentle;
  const still = m.level !== 'full';
  const halo = useAnimatedStyle(() => ({
    opacity: on ? 0.18 + 0.35 * level.value : 0,
    transform: [{ scale: still ? 1 : withSpring(1 + 0.55 * level.value + 0.08 * breathe.value, gentle) }],
  }));
  const core = useAnimatedStyle(() => ({ transform: [{ scale: still ? 1 : withSpring(1 + 0.12 * level.value, gentle) }] }));
  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View style={[{ position: 'absolute', width: size, height: size, borderRadius: size / 2, backgroundColor: c.accent }, halo]} />
      <Animated.View style={[{ width: size * 0.62, height: size * 0.62, borderRadius: size, backgroundColor: on ? c.accent : 'transparent', borderWidth: on ? 0 : 1.25, borderColor: c.text, alignItems: 'center', justifyContent: 'center' }, core]}>
        <View style={{ width: 4, height: size * 0.26, borderRadius: 2, backgroundColor: on ? c.onAccent : c.text }} />
      </Animated.View>
    </View>
  );
}

/** A placeholder bar with a soft passing light instead of the whole block blinking. */
export function Skeleton({ width, height = 12, style }: { width: number | `${number}%`; height?: number; style?: StyleProp<ViewStyle> }) {
  const m = useMotion();
  const { colors: c } = useSession();
  const x = useSharedValue(0);
  useEffect(() => {
    if (m.level === 'off') return;
    x.value = withRepeat(withTiming(1, { duration: m.d.loop * 0.8, easing: Easing.inOut(Easing.quad) }), -1);
    return () => cancelAnimation(x);
  }, [m.level, m.d.loop, x]);
  const a = useAnimatedStyle(() => ({ transform: [{ translateX: -120 + x.value * 360 }] }));
  return (
    <View style={[{ width, height, backgroundColor: c.panel3, overflow: 'hidden' }, style]} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {m.level === 'off' ? null : <Animated.View style={[{ position: 'absolute', top: 0, bottom: 0, width: 120, backgroundColor: c.panel, opacity: 0.6 }, a]} />}
    </View>
  );
}

/** Placeholder rows for a list that's still loading. */
export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <View style={{ paddingTop: 12, gap: 18 }} accessibilityRole="progressbar" accessibilityLabel="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <View key={i} style={{ gap: 7 }}>
          <Skeleton width={`${62 - ((i * 13) % 30)}%`} height={13} />
          <Skeleton width={`${34 - ((i * 7) % 14)}%`} height={10} />
        </View>
      ))}
    </View>
  );
}
