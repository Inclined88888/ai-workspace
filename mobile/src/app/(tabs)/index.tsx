/** Today: the front page. What needs you, what is running, what is next. */
import React from 'react';
import { Pressable, RefreshControl, ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { Home } from '@/lib/api';
import { useRemote, setApprovalsCount } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { masthead } from '@/lib/format';
import { fonts, hairline, space, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { ApprovalCard } from '@/components/ApprovalCard';
import { RunRow } from '@/components/RunRow';
import { ConnectionMark, OfflineNotice } from '@/components/Connection';
import { FadeIn, Label, Notice, Pulse, Rule, Screen, Section, T } from '@/components/ui';

export default function Today() {
  const { colors: c, pairing } = useSession();
  const home = useRemote<Home>(async (cl) => {
    const h = await cl.home();
    setApprovalsCount(h.approvals_count);
    return h;
  }, 6000);
  const h = home.data;
  const name = h?.workspace_name || pairing?.name || 'AI Workspace';

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ paddingBottom: space[12] }}
        refreshControl={<RefreshControl refreshing={home.refreshing} onRefresh={home.refresh} tintColor={c.accent} />}
      >
        <View style={{ paddingHorizontal: space[5], paddingTop: space[4] }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <Label>{masthead()}</Label>
            <ConnectionMark />
          </View>
          <Rule strong style={{ marginTop: space[3] }} />
          <Text numberOfLines={1} adjustsFontSizeToFit style={[type.masthead, { color: c.text, textAlign: 'center', marginTop: space[3], fontSize: 13 }]}>
            {name}
          </Text>
          <Rule style={{ marginTop: space[3] }} />
          <FadeIn>
            <Text style={{ fontFamily: fonts.display, fontSize: 48, lineHeight: 52, color: c.text, marginTop: space[5], letterSpacing: -0.8 }}>Today.</Text>
            <Text style={{ fontFamily: fonts.displayItalic, fontSize: 17, lineHeight: 24, color: c.muted, marginTop: 2 }}>
              {summary(h)}
            </Text>
          </FadeIn>
          <FadeIn delay={60}>
            <Pressable
              onPress={() => {
                tap();
                router.push({ pathname: '/chat/[id]', params: { id: 'new' } });
              }}
              accessibilityRole="button"
              accessibilityLabel="Ask your computer"
              style={({ pressed }) => ({ marginTop: space[5], borderWidth: 1, borderColor: c.rule, paddingVertical: 14, paddingHorizontal: 14, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: pressed ? c.panel2 : c.panel })}
            >
              <T style={{ color: c.muted, fontSize: 16 }}>Ask your computer…</T>
              <Text style={{ fontFamily: fonts.display, fontSize: 20, color: c.accent }}>→</Text>
            </Pressable>
          </FadeIn>
        </View>
        <OfflineNotice />
        {home.error && h ? (
          <View style={{ paddingHorizontal: space[5], paddingTop: space[3] }}>
            <Notice text={home.error} />
          </View>
        ) : null}

        <View style={{ paddingHorizontal: space[5] }}>
          {h && h.approvals.length ? (
            <FadeIn delay={100}>
              <Section title={`Waiting for you · ${h.approvals_count}`} action={h.approvals_count > h.approvals.length ? 'All' : undefined} onAction={() => router.navigate('/approvals')} />
              {h.approvals.map((a) => (
                <ApprovalCard key={a.id} a={a} compact onDecided={() => home.reload()} />
              ))}
            </FadeIn>
          ) : null}

          <FadeIn delay={140}>
            <Section title="On the desk" action="All runs" onAction={() => router.navigate('/runs')} />
            {/* feat/v02-automations: background jobs board and the Share box */}
            <Pressable onPress={() => router.push('/jobs')} accessibilityRole="button" accessibilityLabel="Open the jobs board"
              style={{ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border, flexDirection: 'row', justifyContent: 'space-between' }}>
              <T>Jobs board and Share</T><T style={{ color: c.muted }}>›</T>
            </Pressable>
            {h?.running ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                <Pulse color={c.accent} />
                <T>{h.running === 1 ? 'One job is running on your computer.' : `${h.running} jobs are running on your computer.`}</T>
              </View>
            ) : null}
            {h?.runs.length ? h.runs.slice(0, 4).map((r) => <RunRow key={r.id} r={r} />) : h ? <T style={{ color: c.muted, paddingVertical: space[3] }}>Nothing has run yet.</T> : null}
          </FadeIn>

          {h?.tasks.length ? (
            <FadeIn delay={180}>
              <Section title="To do" />
              {h.tasks.slice(0, 5).map((t) => (
                <TaskRow key={t.id} id={t.id} title={t.title} status={t.status} due={t.due} onChange={() => home.reload()} />
              ))}
            </FadeIn>
          ) : null}

          {h?.notes.length ? (
            <FadeIn delay={220}>
              <Section title="Notes" />
              {h.notes.slice(0, 3).map((n) => (
                <View key={n.id} style={{ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                  <Text style={[type.title, { fontSize: 18, color: c.text }]}>{n.title}</Text>
                  {n.excerpt ? <T numberOfLines={2} style={{ color: c.muted, fontSize: 14, lineHeight: 20, marginTop: 2 }}>{plain(n.excerpt)}</T> : null}
                </View>
              ))}
            </FadeIn>
          ) : null}
        </View>
      </ScrollView>
    </Screen>
  );
}

function summary(h: Home | null): string {
  if (!h) return 'Reading the desk…';
  const parts: string[] = [];
  if (h.approvals_count) parts.push(h.approvals_count === 1 ? 'one thing needs your yes' : `${h.approvals_count} things need your yes`);
  if (h.running) parts.push(h.running === 1 ? 'one job running' : `${h.running} jobs running`);
  const open = h.tasks.filter((t) => t.status !== 'done').length;
  if (open) parts.push(open === 1 ? 'one task open' : `${open} tasks open`);
  if (!parts.length) return 'All quiet. Nothing needs you.';
  const s = parts.join(', ');
  return s.charAt(0).toUpperCase() + s.slice(1) + '.';
}

function TaskRow({ id, title, status, due, onChange }: { id: number; title: string; status: 'todo' | 'doing' | 'done'; due: string | null; onChange: () => void }) {
  const { colors: c, client } = useSession();
  const next = status === 'todo' ? 'doing' : status === 'doing' ? 'done' : 'todo';
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityState={{ checked: status === 'done' }}
      accessibilityLabel={`${title}, ${status}. Tap to mark ${next}.`}
      onPress={async () => {
        tap();
        await client?.setTask(id, next).catch(() => undefined);
        onChange();
      }}
      style={{ flexDirection: 'row', alignItems: 'center', gap: space[3], paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border }}
    >
      <View style={{ width: 14, height: 14, borderWidth: 1, borderColor: c.rule, backgroundColor: status === 'done' ? c.ink : status === 'doing' ? c.blueSoft : 'transparent' }} />
      <T style={{ flex: 1, textDecorationLine: status === 'done' ? 'line-through' : 'none', color: status === 'done' ? c.muted : c.text }}>{title}</T>
      {due ? <Label style={{ fontSize: 9.5 }}>{due.slice(5, 10)}</Label> : status === 'doing' ? <Label style={{ fontSize: 9.5 }} color={c.text}>Doing</Label> : null}
    </Pressable>
  );
}

const plain = (md: string) => md.replace(/[#>*_`]+/g, '').replace(/\s+/g, ' ').trim();
