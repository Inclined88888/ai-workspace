/** One run's receipt: outcome, cost, and the trace of model calls, tool calls and checks under it. */
import React from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { TraceNode } from '@/lib/api';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { modelName, money, outcomeLabel, relativeTime, runTitle, toolName } from '@/lib/format';
import { fonts, hairline, space, type } from '@/lib/theme';
import { Btn, FadeIn, Label, Notice, Rule, Screen, T, Tag } from '@/components/ui';

function Node({ n, depth }: { n: TraceNode; depth: number }) {
  const { colors: c } = useSession();
  const o = outcomeLabel(n.outcome);
  const name = n.tool ? toolName(n.tool) : n.kind === 'model_call' || n.kind === 'llm_call' ? `Model · ${modelName(n.model)}` : n.kind.replace(/_/g, ' ');
  return (
    <View style={{ marginLeft: depth ? 12 : 0, borderLeftWidth: depth ? hairline : 0, borderLeftColor: c.borderStrong, paddingLeft: depth ? 12 : 0 }}>
      <View style={{ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <Text style={[type.bodyMedium, { color: c.text, flex: 1 }]} numberOfLines={1}>{capital(name)}</Text>
          <Tag label={o.label} tone={o.tone} />
        </View>
        {n.latency_ms ? <Text style={[type.small, { color: c.muted, marginTop: 2 }]}>{(n.latency_ms / 1000).toFixed(1)} s{n.total_usd != null ? `  ·  ${money(n.total_usd)}` : ''}</Text> : null}
        {n.args ? <Text numberOfLines={3} style={{ fontFamily: fonts.mono, fontSize: 11.5, color: c.muted, marginTop: 4 }}>{n.args}</Text> : null}
        {n.evidence ? <T numberOfLines={3} style={{ fontSize: 13, lineHeight: 18, marginTop: 4, color: c.ok }}>{n.evidence}</T> : null}
        {n.failure_reason ? <T numberOfLines={4} style={{ fontSize: 13, lineHeight: 18, marginTop: 4, color: c.danger }}>{n.failure_reason}</T> : null}
      </View>
      {(n.children || []).map((ch) => (
        <Node key={ch.id} n={ch} depth={depth + 1} />
      ))}
    </View>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function Run() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { colors: c } = useSession();
  const run = useRemote<TraceNode>((cl) => cl.trace(Number(id)), 4000, id);
  const r = run.data;
  const o = r ? outcomeLabel(r.outcome) : null;
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/runs'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  Runs</Label>
        </Pressable>
        {run.error && !r ? <View style={{ marginTop: space[4] }}><Notice text={run.error} tone="bad" /></View> : null}
        {r && o ? (
          <FadeIn>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: space[4] }}>
              <Label>Receipt № {r.id}{r.total_usd == null ? '  ·  price unknown' : ''}</Label>
              <Label>{relativeTime(r.ts)}</Label>
            </View>
            <Text style={[type.headline, { color: c.text, marginTop: space[2], fontSize: 30, lineHeight: 34 }]}>
              {capital(runTitle(r))}
            </Text>
            <View style={{ marginTop: space[3] }}>
              <Tag label={o.label} tone={o.tone} />
            </View>
            <Rule strong style={{ marginTop: space[4] }} />
            <View style={{ flexDirection: 'row', marginTop: space[3] }}>
              {[
                ['Cost', r.total_usd == null ? '—' : money(r.total_usd)],
                ['Steps', String(r.steps || 0)],
                ['Tools', String(r.tool_calls || 0)],
                ['Time', r.latency_ms ? `${(r.latency_ms / 1000).toFixed(1)}s` : '—'],
              ].map(([k, v], i) => (
                <View key={k} style={{ flex: 1, borderLeftWidth: i ? hairline : 0, borderLeftColor: c.border, paddingLeft: i ? space[3] : 0 }}>
                  <Label style={{ fontSize: 9.5 }}>{k}</Label>
                  <Text style={{ fontFamily: fonts.display, fontSize: 22, color: c.text, marginTop: 2 }}>{v}</Text>
                </View>
              ))}
            </View>
            <Rule style={{ marginTop: space[3] }} />
            {r.model ? <T style={{ color: c.muted, marginTop: space[3], fontSize: 13.5 }}>Model: {modelName(r.model)} · from {r.channel === 'mobile' ? 'a phone' : r.channel === 'web' ? 'the desktop' : r.channel === 'schedule' ? 'a routine' : r.channel}</T> : null}
            {r.failure_reason ? <View style={{ marginTop: space[3] }}><Notice text={r.failure_reason} tone="bad" /></View> : null}
            <Label color={c.text} style={{ marginTop: space[6] }}>Trace</Label>
            <Rule style={{ marginTop: space[2] }} />
            {(r.children || []).length ? r.children.map((ch) => <Node key={ch.id} n={ch} depth={0} />) : <T style={{ color: c.muted, paddingVertical: space[3] }}>No steps recorded.</T>}
            {r.conversation_id ? (
              <Btn label="Open the chat" kind="outline" style={{ marginTop: space[6] }} onPress={() => router.push({ pathname: '/chat/[id]', params: { id: String(r.conversation_id) } })} />
            ) : null}
          </FadeIn>
        ) : null}
      </ScrollView>
    </Screen>
  );
}
