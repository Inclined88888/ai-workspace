/**
 * feat/v02-core pieces that sit inside existing screens: the Home links to Projects and History,
 * the chat's project + "Check answers" bar, and the "Check this" panel under a reply.
 */
import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { Checked, friendlyError, Project, v02, verdictTone } from '@/lib/v02';
import { fonts, hairline, space, type } from '@/lib/theme';
import { success, tap } from '@/lib/haptics';
import { FadeIn, Label, Notice, T, Tag } from '@/components/ui';

/** Two quiet links under "Ask your computer…" on Home. */
export function HomeLinks() {
  const { colors: c } = useSession();
  const link = (label: string, to: '/projects' | '/history') => (
    <Pressable key={to} onPress={() => { tap(); router.push(to); }} accessibilityRole="link" accessibilityLabel={label}
      style={({ pressed }) => ({ flex: 1, borderWidth: hairline, borderColor: c.rule, paddingVertical: 11, paddingHorizontal: 12, backgroundColor: pressed ? c.panel2 : 'transparent' })}>
      <Label color={c.text}>{label}  →</Label>
    </Pressable>
  );
  return <View style={{ flexDirection: 'row', gap: space[2], marginTop: space[3] }}>{[link('Projects', '/projects'), link('History', '/history')]}</View>;
}

/** Under the chat title: which project this chat is filed in (tap to change) and whether answers get checked. */
export function ChatProjectBar({ cid }: { cid: number | null }) {
  const { client, colors: c } = useSession();
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState('');
  // Older computers without v0.2 answer 404: the bar stays hidden.
  const remote = useRemote(async (cl) => {
    const l = await v02.projects(cl);
    const project = cid ? (await v02.projectFor(cl, cid)).project : l.projects.find((p) => p.id === l.active_id) || null;
    return { all: l.projects, activeId: l.active_id, project, check: await v02.checkMode(cl, cid) };
  }, 0, String(cid));
  const d = remote.data;
  const all = d?.all || [];
  const project = d?.project || null;
  const activeId = d?.activeId ?? null;
  const check = d?.check || null;

  if (!all.length && !check) return null;
  const pick = async (p: Project | null) => {
    if (!client) return;
    setOpen(false);
    try {
      if (cid) await v02.moveChat(client, cid, p ? p.id : null);
      else await v02.setActive(client, p ? p.id : null);
      if (d) remote.set({ ...d, project: p, activeId: cid ? d.activeId : p ? p.id : null });
      success();
    } catch (e) {
      setMsg(friendlyError(e));
    }
  };
  const toggle = async () => {
    if (!client || !check) return;
    try {
      const r = await v02.setCheckMode(client, cid, !check.on);
      if (d) remote.set({ ...d, check: { ...check, on: r.on } });
      tap();
    } catch (e) {
      setMsg(friendlyError(e, 'checked_answers'));
    }
  };
  return (
    <View style={{ marginTop: space[2] }}>
      <View style={{ flexDirection: 'row', gap: space[2], flexWrap: 'wrap' }}>
        {all.length ? (
          <Pressable onPress={() => setOpen(!open)} accessibilityRole="button" accessibilityLabel={`Project: ${project?.name || 'none'}. Change`}
            style={{ borderWidth: hairline, borderColor: c.rule, paddingVertical: 5, paddingHorizontal: 9, backgroundColor: project ? c.accentSoft : 'transparent' }}>
            <Text style={[type.small, { color: c.text }]}>{project ? project.name : 'No project'}  ▾</Text>
          </Pressable>
        ) : null}
        {check ? (
          <Pressable onPress={toggle} accessibilityRole="switch" accessibilityState={{ checked: check.on }} accessibilityLabel="Check answers"
            style={{ borderWidth: hairline, borderColor: c.rule, paddingVertical: 5, paddingHorizontal: 9, backgroundColor: check.on ? c.okSoft : 'transparent' }}>
            <Text style={[type.small, { color: c.text }]}>{check.on ? '✓ Checking answers' : 'Check answers'}{check.allowed ? '' : '  · Pro'}</Text>
          </Pressable>
        ) : null}
      </View>
      {open ? (
        <FadeIn style={{ marginTop: space[2], borderWidth: hairline, borderColor: c.rule, backgroundColor: c.panel }}>
          {[...all, null].map((p) => (
            <Pressable key={p ? p.id : 'none'} onPress={() => pick(p)} accessibilityRole="button"
              style={{ paddingVertical: 10, paddingHorizontal: 12, borderBottomWidth: hairline, borderBottomColor: c.border }}>
              <T style={{ fontSize: 14 }}>{p ? p.name : 'No project'}{p && p.id === (project?.id ?? (cid ? -1 : activeId)) ? '  ✓' : ''}</T>
            </Pressable>
          ))}
        </FadeIn>
      ) : null}
      {msg ? <View style={{ marginTop: space[2] }}><Notice text={msg} action="OK" onAction={() => setMsg('')} /></View> : null}
    </View>
  );
}

/** Under a reply: "Check this", then each claim marked backed, partly backed or not backed, with its source. */
export function CheckThis({ cid, content }: { cid: number | null; content: string }) {
  const { client, colors: c } = useSession();
  const [res, setRes] = useState<Checked | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [focus, setFocus] = useState<number | null>(null);
  if (!cid || !content.trim()) return null;
  const run = async () => {
    if (!client) return;
    setBusy(true); setErr('');
    try {
      setRes(await v02.check(client, cid, content));
      success();
    } catch (e) {
      setErr(friendlyError(e, 'checked_answers'));
    } finally {
      setBusy(false);
    }
  };
  if (!res) {
    return (
      <View style={{ marginTop: space[2], gap: space[2] }}>
        <Pressable onPress={run} disabled={busy} accessibilityRole="button" accessibilityLabel="Check this answer" hitSlop={6}>
          <Label color={c.accentText}>{busy ? 'Checking every claim…' : '✓ Check this'}</Label>
        </Pressable>
        {err ? <Notice text={err} /> : null}
      </View>
    );
  }
  const num = (id: string | null) => (id ? id.slice(1) : '');
  const fc = focus !== null ? res.claims[focus] : null;
  const src = fc ? res.sources.find((s) => s.id === fc.source) : undefined;
  return (
    <FadeIn style={{ marginTop: space[3], borderWidth: hairline, borderColor: c.rule, backgroundColor: c.panel, padding: space[3] }}>
      <Text style={[type.bodyMedium, { color: c.text }]}>{res.summary}</Text>
      <Text style={[type.body, { color: c.text, marginTop: space[2] }]}>
        {res.segments.map((s, i) => {
          if (s.claim === undefined) return <Text key={i}>{s.text}</Text>;
          const cl = res.claims[s.claim];
          const bad = cl.verdict === 'unsupported' || cl.verdict === 'contradicted';
          return (
            <Text key={i} onPress={() => setFocus(s.claim!)} accessibilityRole="button" accessibilityLabel={`${cl.label}: ${s.text}`}
              style={{ backgroundColor: bad ? c.dangerSoft : cl.verdict === 'partly' ? c.warnSoft : 'transparent', textDecorationLine: bad ? 'none' : 'underline', color: c.text }}>
              {s.text}<Text style={{ fontSize: 11, color: bad ? c.danger : c.muted }}>{bad ? ' ?' : ` [${num(cl.source)}]`}</Text>
            </Text>
          );
        })}
      </Text>
      {fc ? (
        <View style={{ marginTop: space[3], borderLeftWidth: 2, borderLeftColor: verdictTone(fc.verdict) === 'ok' ? c.ok : verdictTone(fc.verdict) === 'warn' ? c.warn : c.danger, paddingLeft: space[3] }}>
          <Tag label={fc.label} tone={verdictTone(fc.verdict)} />
          {fc.note ? <T style={{ fontSize: 13.5, lineHeight: 19, marginTop: 4 }}>{fc.note}</T> : null}
          {fc.quote ? <Text style={{ fontFamily: fonts.displayItalic, fontSize: 14, color: c.muted, marginTop: 4 }}>“{fc.quote}”</Text> : null}
          {src ? <Text style={[type.small, { color: c.muted, marginTop: 4 }]}>Source {num(src.id)}: {src.label}</Text> : null}
        </View>
      ) : <Text style={[type.small, { color: c.muted, marginTop: space[2] }]}>Tap a sentence to see its source.</Text>}
      {res.sources.length ? (
        <View style={{ marginTop: space[3] }}>
          {res.sources.map((s) => <Text key={s.id} style={[type.small, { color: c.muted }]}>{num(s.id)}. {s.kind} · {s.label}</Text>)}
        </View>
      ) : null}
    </FadeIn>
  );
}
