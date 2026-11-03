/** Teammates on the phone (feat/grok-style): drafts to send (Face ID first), hand off to the computer, browse shared
 *  folders, and the computers this phone is linked to. */
import React, { useEffect, useState } from 'react';
import { Pressable, RefreshControl, ScrollView, Text, TextInput, View } from 'react-native';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { hairline, space, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { confirmWithBiometrics } from '@/lib/guard';
import { enqueue, flush, Queued, send } from '@/lib/link';
import { loadComputers, loadQueue, saveComputers, saveQueue } from '@/lib/storage';
import { newKey } from '@/lib/api';
import { Btn, Empty, Label, Notice, Screen, Section, T, Tag } from '@/components/ui';

type Mate = { id: number; name: string; role: string; avatar: string };
type Draft = { id: number; channel: string; recipients: string; subject: string; body: string; status: string };
type Listing = { teammates: Mate[]; groups: { id: number; name: string }[]; drafts: Draft[] };
type Folder = { path: string; folders: { name: string; path: string }[]; files: { name: string; path: string; size: number }[] };

export default function Teammates() {
  const { client, colors: c } = useSession();
  const data = useRemote<Listing>((cl) => cl.req('GET', '/teammates'), 8000);
  const [path, setPath] = useState('');
  const [folder, setFolder] = useState<Folder | null>(null);
  const [handoff, setHandoff] = useState('');
  const [msg, setMsg] = useState('');
  const [queue, setQueue] = useState<Queued[]>([]);
  const [computers, setComputers] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => { loadQueue().then(setQueue); loadComputers().then((l) => setComputers(l.map((x) => ({ id: x.id, name: x.name })))); }, []);
  useEffect(() => {
    if (!client) return;
    client.req<Folder>('GET', `/files/browse?path=${encodeURIComponent(path)}`).then(setFolder).catch((e) => setMsg(e.message));
  }, [client, path]);

  /** Writes go straight to the computer; if it can't be reached they wait in the queue and go when it's back. */
  const write = async (label: string, method: string, p: string, body?: unknown) => {
    if (!client) return;
    try {
      await client.req(method, p, body);
      setMsg(`${label}: done`);
      data.reload?.();
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status !== 0) { setMsg(e instanceof Error ? e.message : String(e)); return; }
      const list = await loadComputers();
      const q = enqueue(queue, list[0]?.id ?? 'local', { method, path: `/api/mobile${p}`, body, idempotency_key: newKey() }, label);
      setQueue(q); await saveQueue(q);
      setMsg(`${label}: queued until your computer is reachable`);
    }
  };

  const retryQueue = async () => {
    const list = await loadComputers();
    const r = await flush(queue, async (item) => {
      const comp = list.find((x) => x.id === item.computerId);
      if (comp) return send(comp, item.req, (u, i) => fetch(u, i), async (nc) => { await saveComputers(list.map((x) => (x.id === nc.id ? nc : x))); });
      await client!.req(item.req.method, item.req.path.replace('/api/mobile', ''), item.req.body);
      return { seq: 0, status: 200, body: null };
    });
    setQueue(r.left); await saveQueue(r.left);
    setMsg(r.done.length ? `Sent ${r.done.length} waiting item${r.done.length > 1 ? 's' : ''}` : 'Still waiting for your computer');
  };

  const sendDraft = async (d: Draft) => {
    tap();
    const g = await confirmWithBiometrics(`Send to ${d.recipients}`);
    if (!g.ok) { setMsg(g.reason); return; }
    await write('Sent', 'POST', `/outbox/${d.id}/send`);
  };

  const l = data.data;
  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[4], gap: space[3] }} refreshControl={<RefreshControl refreshing={!!data.refreshing} onRefresh={() => data.reload?.()} />}>
        <Text style={[type.title, { color: c.text }]}>Teammates</Text>
        {msg ? <Notice text={msg} tone="ok" /> : null}
        {queue.length ? <Notice text={`${queue.length} waiting to go to your computer`} action="Try now" onAction={retryQueue} /> : null}

        <Section title="Drafts to send" />
        {!l?.drafts.length ? <Empty title="Nothing waiting" body="When a teammate drafts a message, it waits here until you send it." /> : null}
        {l?.drafts.map((d) => (
          <View key={d.id} style={{ paddingVertical: space[2], borderBottomWidth: hairline, borderBottomColor: c.border, gap: 6 }}>
            <Tag label={d.channel} />
            <T style={{ fontWeight: '600' }}>{d.recipients}{d.subject ? ` · ${d.subject}` : ''}</T>
            <T numberOfLines={4} style={{ color: c.muted }}>{d.body}</T>
            <Btn small label="Send" kind="ink" onPress={() => sendDraft(d)} />
          </View>
        ))}

        <Section title="Your teammates" />
        {l?.teammates.map((t) => <T key={t.id}>{t.avatar} {t.name} <Text style={{ color: c.muted }}>{t.role}</Text></T>)}

        <Section title="Hand off to your computer" />
        <TextInput value={handoff} onChangeText={setHandoff} placeholder="Pick this up on my computer…" placeholderTextColor={c.muted} multiline
          style={{ borderWidth: hairline, borderColor: c.border, borderRadius: 10, padding: 10, color: c.text, minHeight: 60 }} />
        <Btn small label="Hand off" disabled={!handoff.trim()} onPress={() => { write('Handed off', 'POST', '/link/handoff', { draft: handoff }); setHandoff(''); }} />

        <Section title="Files on your computer" />
        {path ? <Pressable onPress={() => setPath(path.split('/').slice(0, -1).join('/'))}><Label>← Up</Label></Pressable> : null}
        {folder?.folders.map((f) => <Pressable key={f.path} onPress={() => setPath(f.path)}><T>📁 {f.name}</T></Pressable>)}
        {folder?.files.map((f) => <T key={f.path} style={{ color: c.muted }}>📄 {f.name} · {Math.ceil(f.size / 1024)} KB</T>)}
        {folder && !folder.folders.length && !folder.files.length ? <Empty title="No shared folders" body="Choose folders to share in Channels & phone on your computer." /> : null}

        <Section title="Linked computers" />
        {computers.length ? computers.map((x) => <T key={x.id}>💻 {x.name}</T>) : <T style={{ color: c.muted }}>This computer only. Pair another from its Phone page.</T>}
      </ScrollView>
    </Screen>
  );
}
