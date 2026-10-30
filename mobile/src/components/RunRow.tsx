import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { router } from 'expo-router';
import { RunCard } from '@/lib/api';
import { modelName, money, outcomeLabel, plural, relativeTime, runTitle } from '@/lib/format';
import { useSession } from '@/lib/session';
import { hairline, space, type } from '@/lib/theme';
import { Label, Tag } from './ui';

const CHANNEL: Record<string, string> = { mobile: 'phone', web: 'desktop', schedule: 'routine', agent: 'agent', api: 'api', telegram: 'telegram', cli: 'terminal' };

export function RunRow({ r }: { r: RunCard }) {
  const { colors: c } = useSession();
  const o = outcomeLabel(r.outcome);
  const title = runTitle(r);
  return (
    <Pressable
      onPress={() => router.push({ pathname: '/run/[id]', params: { id: String(r.id) } })}
      accessibilityRole="button"
      accessibilityLabel={`${title}, ${o.label}`}
      style={({ pressed }) => ({ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border, opacity: pressed ? 0.6 : 1 })}
    >
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Tag label={o.label} tone={o.tone} />
        <Label style={{ fontSize: 9.5 }}>{relativeTime(r.ts)}</Label>
      </View>
      <Text numberOfLines={1} style={[type.bodyMedium, { color: c.text, marginTop: 6, fontSize: 16 }]}>{title}</Text>
      <Text numberOfLines={1} style={[type.small, { color: c.muted, marginTop: 2 }]}>
        {[r.channel.startsWith('learn') ? 'background' : CHANNEL[r.channel] ?? r.channel, modelName(r.model), r.steps ? plural(r.steps, 'step') : '', r.tool_calls ? plural(r.tool_calls, 'tool') : '', r.outcome === 'running' ? '' : money(r.total_usd)]
          .filter(Boolean)
          .join('  ·  ')}
      </Text>
      {r.failure_reason ? <Text numberOfLines={2} style={[type.small, { color: c.danger, marginTop: 4 }]}>{r.failure_reason}</Text> : null}
    </Pressable>
  );
}
