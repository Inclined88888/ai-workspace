/** feat/v02-core: Projects on the phone. Pick one to see its deadlines, people, notes and chats, and to make it the one new chats use. */
import React, { useState } from 'react';
import { Pressable, RefreshControl, ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { friendlyError, itemsOf, ProjectDetail, ProjectList, UPGRADE, v02 } from '@/lib/v02';
import { hairline, space, type } from '@/lib/theme';
import { success, tap } from '@/lib/haptics';
import { Btn, Empty, FadeIn, Label, Notice, Screen, Section, T, Tag } from '@/components/ui';

export default function Projects() {
  const { client, colors: c } = useSession();
  const list = useRemote<ProjectList>((cl) => v02.projects(cl), 0);
  const [sel, setSel] = useState<number | null>(null);
  const [msg, setMsg] = useState('');
  const pid = sel ?? list.data?.active_id ?? list.data?.projects[0]?.id ?? null;
  const detail = useRemote<ProjectDetail | null>((cl) => (pid ? v02.project(cl, pid) : Promise.resolve(null)), 0, String(pid));
  const p = detail.data;
  const lim = list.data?.limit;

  const setActive = async (id: number | null) => {
    if (!client) return;
    try {
      await v02.setActive(client, id);
      success();
      list.reload(); detail.reload();
    } catch (e) {
      setMsg(friendlyError(e, 'projects_unlimited'));
    }
  };

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }}
        refreshControl={<RefreshControl refreshing={list.refreshing} onRefresh={() => { list.refresh(); detail.reload(); }} tintColor={c.muted} />}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  Today</Label>
        </Pressable>
        <Label style={{ marginTop: space[4] }} color={c.accentText}>Your work</Label>
        <Text style={[type.headline, { color: c.text, marginTop: space[1] }]}>Projects</Text>
        <T style={{ color: c.muted, marginTop: space[2] }}>One place per job, client, case or course. The assistant uses it whenever you mention the project.</T>
        {list.error && !list.data ? <View style={{ marginTop: space[4] }}><Notice text={list.error} tone="bad" /></View> : null}
        {msg ? <View style={{ marginTop: space[3] }}><Notice text={msg} action="OK" onAction={() => setMsg('')} /></View> : null}
        {list.data && !list.data.projects.length ? <Empty title="No projects yet" body="Start one on your computer: Projects → New project." /> : null}

        <View style={{ marginTop: space[4] }}>
          {list.data?.projects.map((x) => (
            <Pressable key={x.id} onPress={() => { tap(); setSel(x.id); }} accessibilityRole="button" accessibilityState={{ selected: x.id === pid }}
              style={{ paddingVertical: space[3], paddingHorizontal: space[2], borderBottomWidth: hairline, borderBottomColor: c.border, backgroundColor: x.id === pid ? c.accentSoft : 'transparent' }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={[type.bodyMedium, { color: c.text, flex: 1 }]} numberOfLines={1}>{x.name}</Text>
                {x.id === list.data?.active_id ? <Tag label="Active" tone="live" /> : null}
              </View>
              {x.next_deadline ? <Text style={[type.small, { color: c.muted }]}>Next: {x.next_deadline.title} · {x.next_deadline.due}</Text> : null}
            </Pressable>
          ))}
          {lim && !lim.unlimited ? <Text style={[type.small, { color: c.muted, marginTop: space[2] }]}>
            {lim.active > lim.free_limit ? `${lim.active} projects open; the free plan keeps ${lim.free_limit}. ${UPGRADE.projects_unlimited}` : `Free plan: ${lim.active} of ${lim.free_limit} project in use.`}
          </Text> : null}
        </View>

        {p ? (
          <FadeIn key={p.id} style={{ marginTop: space[6] }}>
            <Label color={c.accentText}>{p.kind_label || p.kind}</Label>
            <Text style={[type.title, { color: c.text, marginTop: space[1] }]}>{p.name}</Text>
            {p.description ? <T style={{ color: c.muted, marginTop: space[1] }}>{p.description}</T> : null}
            <View style={{ flexDirection: 'row', gap: space[2], marginTop: space[3], flexWrap: 'wrap' }}>
              {p.active ? <Btn small label="Stop using" onPress={() => setActive(null)} /> : <Btn small kind="accent" label="Work on this" onPress={() => setActive(p.id)} />}
              <Btn small kind="ghost" label="Ask about it" onPress={() => router.push({ pathname: '/chat/[id]', params: { id: 'new' } })} />
            </View>

            {itemsOf(p, 'deadline').length ? <Section title="Deadlines" /> : null}
            {itemsOf(p, 'deadline').map((d) => (
              <View key={d.id} style={{ flexDirection: 'row', paddingVertical: 10, borderBottomWidth: hairline, borderBottomColor: c.border, gap: 8 }}>
                <T style={{ flex: 1, textDecorationLine: d.done ? 'line-through' : 'none', color: d.done ? c.muted : c.text }}>{d.title}</T>
                <Text style={[type.small, { color: d.overdue ? c.danger : c.muted }]}>{d.overdue ? 'Overdue · ' : ''}{d.due || ''}</Text>
              </View>
            ))}
            {itemsOf(p, 'person').length ? <Section title="People" /> : null}
            {itemsOf(p, 'person').map((x) => <Row key={x.id} title={x.title} sub={x.body} />)}
            {itemsOf(p, 'note').length ? <Section title="Notes" /> : null}
            {itemsOf(p, 'note').map((x) => <Row key={x.id} title={x.body || x.title} />)}
            {itemsOf(p, 'chat').length ? <Section title="Chats" /> : null}
            {itemsOf(p, 'chat').map((x) => (
              <Pressable key={x.id} onPress={() => router.push({ pathname: '/chat/[id]', params: { id: x.ref } })} accessibilityRole="link">
                <Row title={x.title} sub={x.auto ? `Filed automatically${x.reason ? `: ${x.reason}` : ''}` : ''} link />
              </Pressable>
            ))}
            {[...itemsOf(p, 'file'), ...itemsOf(p, 'site'), ...itemsOf(p, 'link')].length ? <Section title="Files, sites and links" /> : null}
            {[...itemsOf(p, 'file'), ...itemsOf(p, 'site'), ...itemsOf(p, 'link')].map((x) => <Row key={x.id} title={x.title || x.ref} sub={x.kind} />)}
            {p.facts?.length ? <Section title="What the assistant knows" /> : null}
            {p.facts?.map((f, i) => <Row key={i} title={f.content} />)}
            <Text style={[type.small, { color: c.muted, marginTop: space[4] }]}>Add or remove things on your computer, or ask the assistant to add them.</Text>
          </FadeIn>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

function Row({ title, sub, link }: { title: string; sub?: string; link?: boolean }) {
  const { colors: c } = useSession();
  return (
    <View style={{ paddingVertical: 10, borderBottomWidth: hairline, borderBottomColor: c.border }}>
      <T style={{ color: link ? c.accentText : c.text }} numberOfLines={3}>{title}</T>
      {sub ? <Text style={[type.small, { color: c.muted, marginTop: 2 }]}>{sub}</Text> : null}
    </View>
  );
}
