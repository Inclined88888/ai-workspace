/**
 * One held action, as a slip of paper: what the agent wants to do, the arguments that matter, and two square
 * buttons. Approve is ink, Deny is outlined; the decision is sent once and logged on the computer as a receipt.
 */
import React, { useState } from 'react';
import { Animated, Easing, Text, View } from 'react-native';
import { Approval } from '@/lib/api';
import { argPairs, relativeTime, toolName } from '@/lib/format';
import { useSession } from '@/lib/session';
import { fonts, hairline, motion, space, type } from '@/lib/theme';
import { success, warning } from '@/lib/haptics';
import { Btn, Label, T, Tag } from './ui';
import { DoneTick } from './motion';

export function ApprovalCard({ a, onDecided, compact }: { a: Approval; onDecided?: (a: Approval) => void; compact?: boolean }) {
  const { colors: c, client } = useSession();
  const [busy, setBusy] = useState<'' | 'approve' | 'deny'>('');
  const [err, setErr] = useState('');
  const [done, setDone] = useState<Approval | null>(null);
  const fade = useState(() => new Animated.Value(1))[0];

  const decide = async (approve: boolean) => {
    if (!client || busy) return;
    setBusy(approve ? 'approve' : 'deny');
    setErr('');
    try {
      const r = await client.decide(a.id, approve);
      if (approve) success();
      else warning();
      setDone(r);
      Animated.timing(fade, { toValue: 0, duration: motion.slow, delay: 650, easing: Easing.in(Easing.quad), useNativeDriver: true }).start(() =>
        onDecided?.(r),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  };

  const pairs = argPairs(a.args, compact ? 3 : 6);
  const effect = a.effect === 'external' || a.effect === 'persistent' ? 'warn' : 'quiet';

  return (
    <Animated.View style={{ opacity: fade, paddingVertical: space[4], borderBottomWidth: hairline, borderBottomColor: c.border }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Tag label={a.effect ? a.effect.replace(/_/g, ' ') : 'action'} tone={effect} />
        <Label style={{ fontSize: 9.5 }}>{relativeTime(a.created_at)}</Label>
      </View>
      <Text style={[type.title, { color: c.text, marginTop: space[2], fontSize: 21 }]}>{capital(toolName(a.tool))}</Text>
      {a.reason ? <Text style={{ fontFamily: fonts.displayItalic, fontSize: 15, lineHeight: 21, color: c.muted, marginTop: 2 }}>{a.reason}</Text> : null}
      {pairs.length ? (
        <View style={{ marginTop: space[3], backgroundColor: c.panel, borderLeftWidth: 1.5, borderLeftColor: c.blueSoft, paddingVertical: 8, paddingHorizontal: 10, gap: 4 }}>
          {pairs.map(([k, v]) => (
            <View key={k} style={{ flexDirection: 'row', gap: 10 }}>
              <Label style={{ width: 72, fontSize: 9.5, marginTop: 3 }}>{k}</Label>
              <T style={{ flex: 1, fontSize: 13.5, lineHeight: 19 }} numberOfLines={compact ? 2 : 6}>{v}</T>
            </View>
          ))}
        </View>
      ) : null}
      {err ? <T style={{ color: c.danger, marginTop: space[2], fontSize: 13 }}>{err}</T> : null}
      {done ? (
        <View style={{ marginTop: space[3], flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {done.status === 'denied' || done.status === 'failed' ? <View style={{ width: 8, height: 8, backgroundColor: c.danger }} /> : <DoneTick size={18} haptic={false} />}
          <Label color={c.text}>{done.status === 'denied' ? 'Denied · logged' : done.status === 'failed' ? 'Approved · it failed' : 'Approved · running on your computer'}</Label>
        </View>
      ) : (
        <View style={{ flexDirection: 'row', gap: space[2], marginTop: space[3] }}>
          <Btn label="Approve" kind="ink" style={{ flex: 1 }} busy={busy === 'approve'} disabled={!!busy} onPress={() => decide(true)} />
          <Btn label="Deny" kind="outline" style={{ flex: 1 }} busy={busy === 'deny'} disabled={!!busy} onPress={() => decide(false)} />
        </View>
      )}
    </Animated.View>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
