/** Background jobs board on the phone (feat/v02-automations): see what's running, OK what needs you, pause or stop,
 *  and share text or a link with your "when I share something from my phone" automations. */
import React, { useState } from 'react';
import { Pressable, RefreshControl, ScrollView, Text, TextInput, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { relativeTime } from '@/lib/format';
import { hairline, space, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { ACTION_LABEL, Board, Job, JobAction, jobActions, jobLabel, jobTone, shareBody } from '@/lib/jobs';
import { ApprovalCard } from '@/components/ApprovalCard';
import { Btn, Empty, FadeIn, Label, Notice, Rule, Screen, Section, T, Tag } from '@/components/ui';

function JobRow({ j, onChanged }: { j: Job; onChanged: () => void }) {
  const { client, colors: c } = useSession();
  const [busy, setBusy] = useState<JobAction | ''>('');
  const [err, setErr] = useState('');
  const act = async (a: JobAction) => {
    if (!client) return;
    tap();
    setBusy(a);
    setErr('');
    try { await client.jobAction(j.id, a); onChanged(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(''); }
  };
  return (
    <View style={{ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border, gap: 6 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <Tag label={jobLabel(j.status, j.needs)} tone={jobTone(j.status)} />
        <Label>{relativeTime(j.updated_at)}</Label>
      </View>
      <Text style={[type.title, { color: c.text, fontSize: 19, lineHeight: 24 }]} numberOfLines={3}>{j.title}</Text>
      {j.detail ? <T style={{ color: c.muted, fontSize: 13.5 }}>{j.detail}</T> : null}
      {j.approvals.map((a) => <ApprovalCard key={a.id} a={a} compact onDecided={onChanged} />)}
      {j.status === 'done' && j.result ? <T numberOfLines={4} style={{ fontSize: 13.5 }}>{j.result}</T> : null}
      {err ? <Notice text={err} tone="bad" /> : null}
      {jobActions(j.status, j.needs).length ? (
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          {jobActions(j.status, j.needs).map((a) => (
            <Btn key={a} small label={ACTION_LABEL[a]} kind={a === 'start' ? 'ink' : 'outline'} busy={busy === a} disabled={!!busy} onPress={() => act(a)} />
          ))}
        </View>
      ) : null}
    </View>
  );
}

export default function Jobs() {
  const { client, colors: c } = useSession();
  const board = useRemote<Board>((cl) => cl.jobs(), 4000);
  const b = board.data;
  const [share, setShare] = useState('');
  const [sent, setSent] = useState('');
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (!client || !share.trim()) return;
    tap();
    setSending(true);
    setSent('');
    try {
      const r = await client.share(shareBody(share));
      setSent(r.fired ? `Sent. ${r.fired === 1 ? 'One automation' : `${r.fired} automations`} picked it up.` : 'Sent, but no automation is set up for shares yet. Add one on your computer: "When I share something from my phone, …"');
      setShare('');
      board.reload();
    } catch (e) { setSent(e instanceof Error ? e.message : String(e)); } finally { setSending(false); }
  };

  const cols: [keyof Pick<Board, 'you' | 'running' | 'done'>, string][] = [['you', 'Needs you'], ['running', 'Running'], ['done', 'Done']];
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }}
        refreshControl={<RefreshControl refreshing={board.refreshing} onRefresh={board.refresh} tintColor={c.text} />}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  Today</Label>
        </Pressable>
        <Text style={[type.headline, { color: c.text, marginTop: space[4] }]}>Jobs</Text>
        <T style={{ color: c.muted, marginTop: space[2] }}>Work your computer is doing in the background. Anything that sends, posts, pays, deletes or changes something waits for your OK.</T>
        <Rule strong style={{ marginTop: space[4] }} />

        <FadeIn>
          <Section title="Share with your computer" />
          <TextInput value={share} onChangeText={setShare} placeholder="Paste a link or some text" placeholderTextColor={c.faint} multiline
            accessibilityLabel="Share with your computer"
            style={[type.body, { color: c.text, borderWidth: hairline, borderColor: c.borderStrong, padding: space[3], minHeight: 64, textAlignVertical: 'top' }]} />
          <View style={{ flexDirection: 'row', marginTop: space[2] }}>
            <Btn small kind="ink" label="Send" busy={sending} disabled={!share.trim() || sending} onPress={send} />
          </View>
          {sent ? <T style={{ color: c.muted, marginTop: space[2], fontSize: 13.5 }}>{sent}</T> : null}
        </FadeIn>

        {board.error && !b ? <View style={{ marginTop: space[4] }}><Notice text={board.error} tone="bad" /></View> : null}
        {b ? cols.map(([k, title]) => (
          <FadeIn key={k}>
            <Section title={`${title} · ${b[k].length}`} />
            {b[k].length ? b[k].slice(0, k === 'done' ? 10 : 30).map((j) => <JobRow key={j.id} j={j} onChanged={board.reload} />)
              : <Empty title={k === 'you' ? 'Nothing needs you' : k === 'running' ? 'Nothing running' : 'Nothing finished yet'} />}
          </FadeIn>
        )) : null}
        {b && !b.pro ? <T style={{ color: c.muted, marginTop: space[4], fontSize: 13 }}>The Free plan runs one job at a time. Pro runs several at once.</T> : null}
      </ScrollView>
    </Screen>
  );
}
