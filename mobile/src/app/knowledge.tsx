/** feat/memory-core: search everything, People and Projects on the phone. Read-only; edit on your computer. */
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { Ent, EntDetail, groupHits, groupLinks, Hit, knowledge } from '@/lib/knowledge';
import { hairline, space, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { Empty, FadeIn, Label, Notice, Screen, T, Tag } from '@/components/ui';

type Tab = 'search' | 'person' | 'project';

export default function Knowledge() {
  const { client, colors: c } = useSession();
  const [tab, setTab] = useState<Tab>('search');
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [err, setErr] = useState('');
  const [open, setOpen] = useState<number | null>(null);
  const list = useRemote<Ent[]>((cl) => (tab === 'search' ? Promise.resolve([]) : knowledge.entities(cl, tab)), 0, tab);
  const detail = useRemote<EntDetail | null>((cl) => (open ? knowledge.entity(cl, open) : Promise.resolve(null)), 0, String(open));

  useEffect(() => {
    if (!client || q.trim().length < 2) return;
    const t = setTimeout(() => knowledge.search(client, q.trim()).then((r) => { setHits(r); setErr(''); }).catch((e) => setErr(String(e?.message ?? e))), 220);
    return () => clearTimeout(t);
  }, [q, client]);

  const shown = q.trim().length >= 2 ? hits : null;
  const tabBtn = (id: Tab, label: string) => (
    <Pressable key={id} onPress={() => { tap(); setTab(id); setOpen(null); }} accessibilityRole="tab" accessibilityState={{ selected: tab === id }}
      style={{ paddingVertical: 8, paddingHorizontal: 12, borderBottomWidth: 2, borderBottomColor: tab === id ? c.accent : 'transparent' }}>
      <Text style={[type.bodyMedium, { color: tab === id ? c.text : c.muted }]}>{label}</Text>
    </Pressable>
  );

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }} keyboardShouldPersistTaps="handled">
        <Pressable onPress={() => (open ? setOpen(null) : router.canGoBack() ? router.back() : router.replace('/'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  {open ? 'Back' : 'Today'}</Label>
        </Pressable>
        <Label style={{ marginTop: space[4] }} color={c.accentText}>Memory</Label>
        <Text style={[type.headline, { color: c.text, marginTop: space[1] }]}>What I know</Text>
        <T style={{ color: c.muted, marginTop: space[2] }}>Search your chats, files, notes, people and projects. To change or forget something, use your computer.</T>
        {open && detail.data ? <Detail d={detail.data} onOpen={setOpen} /> : <>
          <View style={{ flexDirection: 'row', gap: space[1], marginTop: space[4], borderBottomWidth: hairline, borderBottomColor: c.rule }} accessibilityRole="tablist">
            {[tabBtn('search', 'Search'), tabBtn('person', 'People'), tabBtn('project', 'Projects')]}
          </View>
          {tab === 'search' ? <>
            <TextInput value={q} onChangeText={setQ} placeholder="Search everything" placeholderTextColor={c.faint} autoCorrect={false} accessibilityLabel="Search everything"
              style={[type.body, { color: c.text, borderWidth: hairline, borderColor: c.border, paddingVertical: 10, paddingHorizontal: 12, marginTop: space[3] }]} />
            {err ? <View style={{ marginTop: space[3] }}><Notice text={err} tone="bad" /></View> : null}
            {shown && !shown.length ? <Empty title="Nothing found" body="Try a name, a job number or a few words from a chat." /> : null}
            {shown ? groupHits(shown).map(([g, xs], i) => (
              <FadeIn key={g} delay={i * 30} style={{ marginTop: space[4] }}>
                <Label color={c.faint}>{g}</Label>
                {xs.map((h) => (
                  <Pressable key={`${h.kind}-${h.ref}`} disabled={h.kind !== 'entity'} onPress={() => { tap(); setOpen(Number(h.ref)); }}
                    style={{ paddingVertical: space[2], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                    <Text style={[type.body, { color: c.text }]} numberOfLines={2}>{h.title || 'Untitled'}</Text>
                  </Pressable>
                ))}
              </FadeIn>
            )) : null}
          </> : <>
            {list.error ? <View style={{ marginTop: space[3] }}><Notice text={list.error} tone="bad" /></View> : null}
            {list.data && !list.data.length ? <Empty title={tab === 'person' ? 'No people yet' : 'No projects yet'} body="They appear as they come up in your work." /> : null}
            {list.data?.map((e, i) => (
              <FadeIn key={e.id} delay={Math.min(i, 8) * 25}>
                <Pressable onPress={() => { tap(); setOpen(e.id); }} accessibilityRole="button"
                  style={{ paddingVertical: space[3], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                  <Text style={[type.bodyMedium, { color: c.text }]}>{e.name}</Text>
                  {e.sub ? <Text style={[type.small, { color: c.muted }]}>{e.sub}</Text> : null}
                </Pressable>
              </FadeIn>
            ))}
          </>}
        </>}
      </ScrollView>
    </Screen>
  );
}

function Detail({ d, onOpen }: { d: EntDetail; onOpen: (id: number) => void }) {
  const { colors: c } = useSession();
  const sub = Object.values(d.attrs).filter(Boolean).join(' · ');
  return (
    <FadeIn style={{ marginTop: space[4] }}>
      <Text style={[type.title, { color: c.text }]}>{d.name}</Text>
      {sub ? <Text style={[type.small, { color: c.muted, marginTop: 2 }]}>{sub}</Text> : null}
      <Label style={{ marginTop: space[4] }} color={c.faint}>What I know</Label>
      {d.memories.length ? d.memories.map((m) => (
        <View key={m.id} style={{ paddingVertical: space[2], borderBottomWidth: hairline, borderBottomColor: c.border }}>
          <Text style={[type.body, { color: m.sensitive ? c.muted : c.text }]}>{m.content}</Text>
          <View style={{ flexDirection: 'row', gap: 6, marginTop: 4 }}>{m.sensitive ? <Tag label="Sensitive" tone="warn" /> : null}{m.you_told_me ? <Tag label="You told me" /> : null}</View>
        </View>
      )) : <T style={{ color: c.muted }}>Nothing yet.</T>}
      {groupLinks(d.links).map(([label, xs]) => (
        <View key={label} style={{ marginTop: space[4] }}>
          <Label color={c.faint}>{label}</Label>
          {xs.map((l) => (
            <Pressable key={l.id} onPress={() => { tap(); onOpen(l.id); }} style={{ paddingVertical: space[2], borderBottomWidth: hairline, borderBottomColor: c.border }}>
              <Text style={[type.body, { color: c.text }]}>{l.name}</Text>
            </Pressable>
          ))}
        </View>
      ))}
    </FadeIn>
  );
}
