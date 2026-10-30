/** Packs on the phone (feat/v02-packs): which are added, and the two things worth doing away from the desk. */
import React from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { packsApi, PackRow } from '@/lib/packs';
import { useSession } from '@/lib/session';
import { hairline, space, type } from '@/lib/theme';
import { Empty, FadeIn, Label, Notice, Screen, T, Tag } from '@/components/ui';

const OPEN: Record<string, { to: string; what: string } | undefined> = {
  study: { to: '/packs/study', what: 'Review the flashcards due today' },
  trade: { to: '/packs/trade', what: 'Your quotes and invoices, and reminder drafts' },
};

export default function Packs() {
  const { colors: c } = useSession();
  const list = useRemote<PackRow[]>((cl) => packsApi(cl).list());
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/settings'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  Back</Label>
        </Pressable>
        <Label style={{ marginTop: space[5] }}>Your work</Label>
        <Text style={[type.headline, { color: c.text, marginTop: space[2] }]}>Packs</Text>
        <T style={{ color: c.muted, marginTop: space[2] }}>Add or remove packs on your computer. The ones you’ve added show here.</T>
        {list.error ? <View style={{ marginTop: space[4] }}><Notice text={list.error} tone="bad" /></View> : null}
        {list.data && !list.data.some((p) => p.installed) ? (
          <Empty title="No packs added yet" body="Open Packs on your computer and add one. It shows up here straight away." />
        ) : null}
        {(list.data || []).filter((p) => p.installed).map((p, i) => {
          const o = OPEN[p.id];
          return (
            <FadeIn key={p.id} delay={i * 40}>
              <Pressable disabled={!o} onPress={() => o && router.push(o.to as never)} accessibilityRole="button"
                style={({ pressed }) => ({ paddingVertical: space[4], borderBottomWidth: hairline, borderBottomColor: c.border, opacity: pressed ? 0.7 : 1 })}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text style={[type.title, { color: c.text }]}>{p.name}</Text>
                  <Tag label={p.tier === 'pro' ? 'Pro' : 'Free'} tone={p.tier === 'pro' ? 'live' : 'ok'} />
                </View>
                <T style={{ color: c.muted, marginTop: 4 }}>{o ? o.what : `${p.tagline} Open it on your computer.`}</T>
              </Pressable>
            </FadeIn>
          );
        })}
      </ScrollView>
    </Screen>
  );
}
