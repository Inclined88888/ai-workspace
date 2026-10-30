import React from 'react';
import { Pressable, View } from 'react-native';
import { useSession } from '@/lib/session';
import { Label, Notice, Pulse } from './ui';

/** "● On Wi-Fi · Studio Mac" in the masthead corner. */
export function ConnectionMark() {
  const { colors: c, status, pairing, reconnect } = useSession();
  const relay = pairing?.active.startsWith('https://');
  const text = status === 'online' ? (relay ? 'Via relay' : 'On Wi-Fi') : status === 'connecting' ? 'Connecting' : 'Offline';
  const color = status === 'online' ? c.ok : status === 'connecting' ? c.muted : c.accent;
  return (
    <Pressable onPress={reconnect} hitSlop={10} accessibilityRole="button" accessibilityLabel={`Connection: ${text}. Tap to retry.`} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      {status === 'connecting' ? <Pulse color={color} /> : <View style={{ width: 6, height: 6, backgroundColor: color }} />}
      <Label style={{ fontSize: 9.5 }}>{text}</Label>
    </Pressable>
  );
}

export function OfflineNotice() {
  const { status, reconnect, pairing } = useSession();
  if (status !== 'offline') return null;
  return (
    <View style={{ paddingHorizontal: 20, paddingTop: 12 }}>
      <Notice text={`Can’t reach ${pairing?.name ?? 'your computer'}. Is it awake, with AI Workspace open, on this Wi-Fi?`} action="Retry" onAction={reconnect} />
    </View>
  );
}
