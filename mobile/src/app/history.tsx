/** feat/v02-core: everything the assistant, automations and helpers did, with one-tap undo where it can be undone. */
import React, { useState } from 'react';
import { Alert, Platform, Pressable, RefreshControl, ScrollView, Text, TextInput, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { byDay, friendlyError, HistoryEntry, HistoryPage, needsConfirm, v02 } from '@/lib/v02';
import { relativeTime } from '@/lib/format';
import { fonts, hairline, space, type } from '@/lib/theme';
import { success, warning } from '@/lib/haptics';
import { Btn, Empty, FadeIn, Label, Notice, Screen, Section, T } from '@/components/ui';

function ask(title: string, body: string): Promise<boolean> {
  if (Platform.OS === 'web') return Promise.resolve(typeof window !== 'undefined' && window.confirm(`${title}\n\n${body}`));
  return new Promise((done) => Alert.alert(title, body, [{ text: 'Keep it', style: 'cancel', onPress: () => done(false) }, { text: 'Undo', style: 'destructive', onPress: () => done(true) }]));
}

export default function History() {
  const { client, colors: c } = useSession();
  const [q, setQ] = useState('');
  const page = useRemote<HistoryPage>((cl) => v02.history(cl, null, q), 0, q);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ text: string; tone: 'ok' | 'bad' } | null>(null);

  const undo = async (e: HistoryEntry) => {
    if (!client) return;
    let confirm = false;
    if (e.undo.confirm) {
      if (!(await ask(`Undo “${e.title}”?`, e.undo.confirm))) return;
      confirm = true;
    }
    setBusy(e.id);
    try {
      const r = await v02.undo(client, e.id, confirm);
      success();
      setMsg({ text: r.undo?.note || 'Undone.', tone: 'ok' });
      page.reload();
    } catch (err) {
      if (needsConfirm(err) && !confirm && (await ask(`Undo “${e.title}”?`, e.undo.confirm || 'This changes something outside the app.'))) {
        try { await v02.undo(client, e.id, true); success(); page.reload(); } catch (e2) { setMsg({ text: friendlyError(e2), tone: 'bad' }); }
      } else {
        warning();
        setMsg({ text: friendlyError(err), tone: 'bad' });
      }
    } finally {
      setBusy('');
    }
  };

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }} keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={page.refreshing} onRefresh={page.refresh} tintColor={c.muted} />}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  Today</Label>
        </Pressable>
        <Label style={{ marginTop: space[4] }} color={c.accentText}>Everything that happened</Label>
        <Text style={[type.headline, { color: c.text, marginTop: space[1] }]}>History</Text>
        <T style={{ color: c.muted, marginTop: space[2] }}>Undo what can be undone in one tap. For the rest, here’s how to fix it.</T>
        <TextInput value={q} onChangeText={setQ} placeholder="Search your history" placeholderTextColor={c.faint} accessibilityLabel="Search your history"
          style={{ marginTop: space[4], borderWidth: 1, borderColor: c.rule, backgroundColor: c.panel, paddingHorizontal: 12, paddingVertical: 10, fontFamily: fonts.ui, fontSize: 15, color: c.text }} />
        {msg ? <View style={{ marginTop: space[3] }}><Notice text={msg.text} tone={msg.tone} action="OK" onAction={() => setMsg(null)} /></View> : null}
        {page.error && !page.data ? <View style={{ marginTop: space[3] }}><Notice text={page.error} tone="bad" /></View> : null}
        {page.data && !page.data.entries.length ? <Empty title={q ? 'Nothing matches' : 'Nothing yet'} body={q ? undefined : 'When the assistant does something for you, it shows up here.'} /> : null}
        {byDay(page.data?.entries || []).map(([day, rows]) => (
          <View key={day}>
            <Section title={day} />
            {rows.map((e) => (
              <FadeIn key={e.id} style={{ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border, opacity: e.status === 'undone' ? 0.7 : 1 }}>
                <Text style={[type.bodyMedium, { color: c.text, textDecorationLine: e.status === 'undone' ? 'line-through' : 'none' }]}>{e.title}</Text>
                <Text style={[type.small, { color: c.muted, marginTop: 2 }]}>{e.who} · {relativeTime(e.at)}</Text>
                {e.detail ? <T numberOfLines={3} style={{ fontSize: 13.5, lineHeight: 19, color: c.muted, marginTop: 4 }}>{e.detail}</T> : null}
                {e.status === 'undone' ? <Text style={[type.small, { color: c.ok, marginTop: 4 }]}>Undone{e.undo.note ? `: ${e.undo.note}` : ''}</Text> : null}
                {e.status === 'undo_failed' ? <Text style={[type.small, { color: c.danger, marginTop: 4 }]}>Couldn’t undo: {e.undo.note}</Text> : null}
                {e.fix ? <View style={{ marginTop: space[2], backgroundColor: c.warnSoft, padding: space[2] }}><T style={{ fontSize: 13.5, lineHeight: 19 }}><Text style={{ fontFamily: fonts.uiSemi }}>Can’t undo. </Text>{e.fix}</T></View> : null}
                <View style={{ flexDirection: 'row', gap: space[2], marginTop: space[2] }}>
                  {e.undo.available ? <Btn small label="Undo" busy={busy === e.id} disabled={!!busy} onPress={() => undo(e)} /> : null}
                  {e.conversation_id ? <Btn small kind="ghost" label="Open chat" onPress={() => router.push({ pathname: '/chat/[id]', params: { id: String(e.conversation_id) } })} /> : null}
                </View>
              </FadeIn>
            ))}
          </View>
        ))}
      </ScrollView>
    </Screen>
  );
}
