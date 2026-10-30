/**
 * The editorial kit: paper screens, a magazine masthead, hairline rules, square buttons, quiet status tags,
 * and the one motion we use (a short fade with a 6px lift, like turning to a new page).
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Easing,
  Pressable,
  PressableProps,
  StyleProp,
  StyleSheet,
  Text,
  TextProps,
  TextStyle,
  View,
  ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useColors } from '@/lib/session';
import { fonts, hairline, motion, space, type } from '@/lib/theme';
import { Tone } from '@/lib/format';

export function Screen({ children, style, edges = true }: { children: React.ReactNode; style?: StyleProp<ViewStyle>; edges?: boolean }) {
  const c = useColors();
  const insets = useSafeAreaInsets();
  return <View style={[{ flex: 1, backgroundColor: c.paper, paddingTop: edges ? insets.top : 0 }, style]}>{children}</View>;
}

export function T({ style, ...p }: TextProps & { style?: StyleProp<TextStyle> }) {
  const c = useColors();
  return <Text {...p} style={[type.body, { color: c.text }, style]} />;
}

export function Label({ children, style, color }: { children: React.ReactNode; style?: StyleProp<TextStyle>; color?: string }) {
  const c = useColors();
  return <Text style={[type.label, { color: color ?? c.muted }, style]}>{children}</Text>;
}

export function Rule({ strong, style }: { strong?: boolean; style?: StyleProp<ViewStyle> }) {
  const c = useColors();
  return <View style={[{ height: strong ? 1.5 : hairline, backgroundColor: strong ? c.rule : c.border }, style]} />;
}

/** Page head: small tracked kicker, a Didone headline, an optional italic deck, then a heavy rule. */
export function Masthead({ kicker, title, deck, right }: { kicker?: string; title: string; deck?: string; right?: React.ReactNode }) {
  const c = useColors();
  return (
    <View style={{ paddingHorizontal: space[5], paddingTop: space[4] }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 18 }}>
        {kicker ? <Label>{kicker}</Label> : <View />}
        {right}
      </View>
      <Text style={[type.headline, { color: c.text, marginTop: space[2] }]} accessibilityRole="header">
        {title}
      </Text>
      {deck ? <Text style={{ fontFamily: fonts.displayItalic, fontSize: 16, lineHeight: 22, color: c.muted, marginTop: space[1] }}>{deck}</Text> : null}
      <Rule strong style={{ marginTop: space[3] }} />
    </View>
  );
}

/** Section header inside a page: label left, an optional link right, hairline under. */
export function Section({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) {
  const c = useColors();
  return (
    <View style={{ marginTop: space[6] }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', paddingBottom: space[2] }}>
        <Label color={c.text}>{title}</Label>
        {action ? (
          <Pressable onPress={onAction} hitSlop={10} accessibilityRole="link">
            <Label color={c.accentText}>{action}</Label>
          </Pressable>
        ) : null}
      </View>
      <Rule />
    </View>
  );
}

type BtnKind = 'ink' | 'accent' | 'outline' | 'ghost' | 'blue';

export function Btn({
  label,
  kind = 'outline',
  busy,
  small,
  style,
  icon,
  ...p
}: PressableProps & { label: string; kind?: BtnKind; busy?: boolean; small?: boolean; style?: StyleProp<ViewStyle>; icon?: React.ReactNode }) {
  const c = useColors();
  const bg = { ink: c.ink, accent: c.accent, outline: 'transparent', ghost: 'transparent', blue: c.blueSoft }[kind];
  const fg = { ink: c.onInk, accent: c.onAccent, outline: c.text, ghost: c.text, blue: c.text }[kind];
  const border = kind === 'outline' ? c.text : kind === 'blue' ? c.blueSoft : bg;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      {...p}
      style={({ pressed }) => [
        {
          backgroundColor: bg,
          borderColor: border,
          borderWidth: kind === 'ghost' ? 0 : 1,
          paddingVertical: small ? 8 : 13,
          paddingHorizontal: small ? 12 : 18,
          alignItems: 'center',
          justifyContent: 'center',
          flexDirection: 'row',
          gap: 8,
          opacity: p.disabled ? 0.45 : pressed ? 0.72 : 1,
        },
        style,
      ]}
    >
      {busy ? <ActivityIndicator size="small" color={fg} /> : icon}
      <Text style={[type.label, { color: fg, fontSize: small ? 10.5 : 11.5, letterSpacing: 1.6 }]}>{label}</Text>
    </Pressable>
  );
}

export function Tag({ label, tone = 'quiet' }: { label: string; tone?: Tone }) {
  const c = useColors();
  const map: Record<Tone, [string, string]> = {
    ok: [c.okSoft, c.ok],
    warn: [c.warnSoft, c.warn],
    bad: [c.dangerSoft, c.danger],
    live: [c.blueSoft, c.text],
    quiet: [c.panel2, c.muted],
  };
  const [bg, fg] = map[tone];
  return (
    <View style={{ backgroundColor: bg, paddingHorizontal: 7, paddingVertical: 3, alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 5 }}>
      {tone === 'live' ? <Pulse color={c.accent} /> : null}
      <Text style={[type.label, { color: fg, fontSize: 9.5, letterSpacing: 1.2 }]}>{label}</Text>
    </View>
  );
}

/** A small square that breathes while something is running. */
export function Pulse({ color, size = 6 }: { color: string; size?: number }) {
  const v = useState(() => new Animated.Value(0.25))[0];
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(v, { toValue: 1, duration: 700, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(v, { toValue: 0.25, duration: 700, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [v]);
  return <Animated.View style={{ width: size, height: size, backgroundColor: color, opacity: v }} />;
}

/** The page-turn: fade in with a short lift. `delay` staggers lists (keep it under ~6 items). */
export function FadeIn({ children, delay = 0, style, from = 6 }: { children: React.ReactNode; delay?: number; style?: StyleProp<ViewStyle>; from?: number }) {
  const v = useState(() => new Animated.Value(0))[0];
  useEffect(() => {
    Animated.timing(v, { toValue: 1, duration: motion.slow, delay, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  }, [v, delay]);
  return (
    <Animated.View style={[style, { opacity: v, transform: [{ translateY: v.interpolate({ inputRange: [0, 1], outputRange: [from, 0] }) }] }]}>
      {children}
    </Animated.View>
  );
}

export function Empty({ title, body, children }: { title: string; body?: string; children?: React.ReactNode }) {
  const c = useColors();
  return (
    <FadeIn style={{ paddingHorizontal: space[5], paddingVertical: space[10], alignItems: 'flex-start' }}>
      <Text style={{ fontFamily: fonts.displayItalic, fontSize: 24, lineHeight: 30, color: c.text }}>{title}</Text>
      {body ? <T style={{ color: c.muted, marginTop: space[2] }}>{body}</T> : null}
      {children ? <View style={{ marginTop: space[4] }}>{children}</View> : null}
    </FadeIn>
  );
}

/** Thin banner for "computer offline" and errors. Never red-filled: a red rule on paper. */
export function Notice({ text, action, onAction, tone = 'warn' }: { text: string; action?: string; onAction?: () => void; tone?: 'warn' | 'bad' | 'ok' }) {
  const c = useColors();
  const line = tone === 'ok' ? c.ok : tone === 'bad' ? c.danger : c.accent;
  return (
    <View style={{ borderLeftWidth: 2, borderLeftColor: line, backgroundColor: c.panel, paddingVertical: 10, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <T style={{ flex: 1, fontSize: 13.5, lineHeight: 19 }}>{text}</T>
      {action ? (
        <Pressable onPress={onAction} hitSlop={8} accessibilityRole="button">
          <Label color={c.accentText}>{action}</Label>
        </Pressable>
      ) : null}
    </View>
  );
}

export const s = StyleSheet.create({
  pad: { paddingHorizontal: space[5] },
  row: { flexDirection: 'row', alignItems: 'center' },
});
