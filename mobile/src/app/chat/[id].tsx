/**
 * A conversation. Replies stream in from the computer (status line, tool receipts, held actions inline), then the
 * answer is revealed word by word. Compose with the keyboard, on-device dictation, a photo or a file.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Image, KeyboardAvoidingView, type ScrollViewInstance, Platform, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Message, newKey } from '@/lib/api';
import { useSession } from '@/lib/session';
import { revealSteps } from '@/lib/reveal';
import { decisionNote, initialTurn, phaseLabel, reduceTurn, splitAttachmentNotes, HeldAction, Turn } from '@/lib/turn';
import { toolName } from '@/lib/format';
import { pickFile, pickPhoto, Pending } from '@/lib/attach';
import { startDictation, VoiceSession } from '@/lib/voice';
import { approvalPrompt, canSpeak, say, stopSaying } from '@/lib/voiceMode'; // feat/v02-anywhere-voice
import { fonts, hairline, space, type } from '@/lib/theme';
import { error as hError, success, tap } from '@/lib/haptics';
import { Markdown } from '@/components/Markdown';
import { ApprovalCard } from '@/components/ApprovalCard';
import { FadeIn, Label, Notice, Rule, Screen, T } from '@/components/ui';
import { DoneTick, Node, SpringIn, Thinking, VoiceOrb } from '@/components/motion';
import { useSharedValue } from 'react-native-reanimated';
import { volumeLevel } from '@/lib/motion';

type Conv = { id: number; title: string; messages: Message[] };

export default function Chat() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { colors: c, client, prefs, status } = useSession();
  const insets = useSafeAreaInsets();
  const [conv, setConv] = useState<Conv | null>(null);
  const [cid, setCid] = useState<number | null>(id && id !== 'new' ? Number(id) : null);
  const [loadErr, setLoadErr] = useState('');
  const [draft, setDraft] = useState('');
  const [firstLine, setFirstLine] = useState(''); // a new chat's title until the computer names it
  const [files, setFiles] = useState<Pending[]>([]);
  const [turn, setTurn] = useState<Turn | null>(null);
  const [pendingText, setPendingText] = useState('');
  const [shown, setShown] = useState('');
  const [note, setNote] = useState('');
  const [listening, setListening] = useState(false);
  const level = useSharedValue(0);
  const [attachOpen, setAttachOpen] = useState(false);
  const scroll = useRef<ScrollViewInstance>(null);
  const voice = useRef<VoiceSession | null>(null);
  const stream = useRef<{ abort: () => void } | null>(null);
  // feat/v02-anywhere-voice: voice mode (Pro) — talk, hear the answer, answer approvals out loud
  const [talk, setTalk] = useState(false);
  const talkRef = useRef(false);
  const [asking, setAsking] = useState<HeldAction | null>(null);
  const askingRef = useRef<number | null>(null); // cleared when the card's buttons answer it first

  const load = useCallback(
    async (convId: number | null) => {
      if (!client || !convId) return;
      try {
        const r = await client.conversation(convId);
        setConv({ id: r.id, title: r.title, messages: r.messages });
        setLoadErr('');
      } catch (e) {
        setLoadErr(e instanceof Error ? e.message : String(e));
      }
    },
    [client],
  );

  useEffect(() => {
    // Fetching on connect is the point of this effect; load() sets state when the answer arrives.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (status === 'online') load(cid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, load]);

  useEffect(() => () => {
    stream.current?.abort();
    voice.current?.cancel();
    talkRef.current = false;
    stopSaying();
  }, []);

  // Reveal the answer at reading pace once it arrives.
  const answer = turn?.text ?? '';
  const revealEnd = useRef(0);
  useEffect(() => {
    if (!answer) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clearing the reveal when the live turn ends
      setShown('');
      return;
    }
    const steps = revealSteps(answer);
    let i = 0;
    const t = setInterval(() => {
      setShown(answer.slice(0, steps[i]));
      i += 1;
      if (i >= steps.length) clearInterval(t);
    }, 16);
    return () => clearInterval(t);
  }, [answer]);

  const finishTurn = async (convId: number | null, ok: boolean) => {
    if (ok) success();
    // Let the reveal finish before the saved transcript replaces the live turn.
    const left = revealEnd.current - Date.now();
    if (left > 0) await new Promise((r) => setTimeout(r, left + 120));
    await load(convId);
    // keep the live turn visible until the reloaded transcript contains it
    setTurn((t) => (t && t.phase === 'error' ? t : null));
    setPendingText('');
  };

  const send = async (spoken?: string): Promise<Turn | null> => {
    const text = (spoken ?? draft).trim();
    if (!client || (!text && !files.length) || (turn && !['done', 'error'].includes(turn.phase))) return null;
    voice.current?.stop();
    tap();
    setNote('');
    setDraft('');
    setPendingText(text || (files.length ? '' : ''));
    if (!cid && !conv) setFirstLine(text.split('\n')[0].slice(0, 80));
    const sending = files;
    setFiles([]);
    setAttachOpen(false);
    let t = initialTurn(cid);
    setTurn(t);
    let ids: string[] = [];
    try {
      ids = await Promise.all(sending.map((f) => client.attach({ kind: f.kind, mime: f.mime, name: f.name, data: f.data }).then((r) => r.id)));
    } catch (e) {
      hError();
      setTurn({ ...t, phase: 'error', error: `Attachment upload failed: ${e instanceof Error ? e.message : e}` });
      setFiles(sending);
      setDraft(text);
      return null;
    }
    const h = client.chat(
      { message: text, conversation_id: cid, model: prefs.model || null, attachments: ids },
      (e) => {
        t = reduceTurn(t, e);
        setTurn(t);
        // 'done' usually lands in the same chunk as the message, before the reveal effect runs: book the reveal time now.
        if (e.type === 'message') revealEnd.current = Date.now() + revealSteps(e.content).length * 16;
        if (e.type === 'start' && !cid) {
          setCid(e.conversation_id);
          router.setParams({ id: String(e.conversation_id) });
        }
      },
      newKey(),
    );
    stream.current = h;
    try {
      await h.done;
      await finishTurn(t.conversationId, t.phase !== 'error');
    } catch (e) {
      hError();
      const msg = e instanceof Error ? e.message : String(e);
      setTurn({ ...t, phase: 'error', error: msg });
      setNote('Your computer keeps working if the connection drops. Pull down on Runs to see how it ended.');
      load(t.conversationId);
    } finally {
      stream.current = null;
    }
    return t;
  };

  // ---- feat/v02-anywhere-voice: voice mode --------------------------------------------------------------------------
  const hearOnce = () =>
    new Promise<string>((resolve) => {
      let last = '';
      let ended = false;
      startDictation(
        'en-AU',
        (text) => {
          last = text;
          setDraft(text);
        },
        (err) => {
          ended = true;
          setListening(false);
          voice.current = null;
          if (err) setNote(err);
          resolve(err ? '' : last.trim());
        },
      ).then((v) => {
        voice.current = v;
        setListening(!!v && !ended);
        if (!v) resolve('');
      });
    });

  /** A held action: the card is on screen, the request is read out, and only a clear spoken yes approves it. */
  const confirmAloud = async (h: HeldAction) => {
    if (!client) return;
    setAsking(h);
    askingRef.current = h.id;
    let prompt = approvalPrompt(h.tool);
    for (let tries = 0; tries < 3 && talkRef.current; tries++) {
      await say(prompt);
      const heard = await hearOnce();
      setDraft('');
      if (!talkRef.current || askingRef.current !== h.id) return;
      if (!heard) {
        prompt = "I didn't catch that. Say yes, send it, or no.";
        continue;
      }
      try {
        const r = await client.voiceDecide(h.id, heard);
        if (r.decision === 'unclear') {
          prompt = r.say || 'Sorry, I need a clear yes or no.';
          continue;
        }
        setAsking(null);
        if (r.decision === 'approve') success();
        await say(r.decision === 'approve' ? (r.status === 'failed' ? "That didn't work. The details are on screen." : 'Done.') : "OK, I won't do that.");
        load(cid);
        return;
      } catch (e) {
        setNote(e instanceof Error ? e.message : String(e)); // e.g. already answered with the buttons
        setAsking(null);
        return;
      }
    }
    if (talkRef.current) await say("I'll leave it on screen. Tap Approve or Deny when you're ready.");
  };

  const talkLoop = async () => {
    while (talkRef.current) {
      const heard = await hearOnce();
      if (!talkRef.current) break;
      if (!heard) continue;
      if (/^(stop|stop listening|goodbye|bye|that's all)\.?$/i.test(heard)) break;
      const t = await send(heard);
      if (!t || !talkRef.current) continue;
      if (t.text) await say(t.text);
      for (const h of t.held.filter((x) => !x.denied)) await confirmAloud(h);
    }
    talkRef.current = false;
    setTalk(false);
  };

  const toggleTalk = async () => {
    if (talk) {
      talkRef.current = false;
      setTalk(false);
      voice.current?.stop(); // ends the listening promise; the loop sees voice mode is off
      stopSaying();
      return;
    }
    if (!client) return;
    try {
      const st = await client.voiceStatus();
      if (!st.features.voice_conversation?.allowed) {
        setNote('Voice mode is part of Pro. Dictation (the Voice button) stays free.');
        return;
      }
    } catch {
      /* an older computer without voice: dictation and reading out still work */
    }
    if (!canSpeak()) setNote('Answers will show on screen; reading them out needs the latest app build.');
    tap();
    talkRef.current = true;
    setTalk(true);
    talkLoop();
  };

  const dictate = async () => {
    if (listening) {
      voice.current?.stop();
      return;
    }
    tap();
    const before = draft ? `${draft.trimEnd()} ` : '';
    setNote('');
    voice.current = await startDictation(
      'en-AU',
      (text) => setDraft(before + text),
      (err) => {
        setListening(false);
        level.value = 0;
        voice.current = null;
        if (err) setNote(err);
      },
      (v) => {
        level.value = volumeLevel(v);
      },
    );
    setListening(!!voice.current);
  };

  const attach = async (how: 'camera' | 'library' | 'file') => {
    setAttachOpen(false);
    try {
      const r = how === 'file' ? await pickFile() : await pickPhoto(how === 'camera');
      if (typeof r === 'string') setNote(r);
      else if (r) setFiles((f) => [...f, r].slice(0, 6));
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
  };

  const busy = !!turn && !['done', 'error'].includes(turn.phase);
  const messages = (conv?.messages ?? []).filter((m) => m.role !== 'tool' || m.name);
  const canSend = !!client && status === 'online' && !busy && (!!draft.trim() || files.length > 0);

  return (
    <Screen>
      {/* Head */}
      <View style={{ paddingHorizontal: space[5], paddingTop: space[3], paddingBottom: space[3] }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
          <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/chats'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
            <Label color={c.text}>‹  Chats</Label>
          </Pressable>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space[3] }}>
            {/* feat/v02-anywhere-voice */}
            <Pressable onPress={toggleTalk} hitSlop={10} accessibilityRole="button" accessibilityState={{ selected: talk }} accessibilityLabel={talk ? 'Stop voice mode' : 'Voice mode: talk and hear answers'} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              {talk ? <Pulse color={c.accent} size={7} /> : null}
              <Label color={talk ? c.accentText : c.text}>{talk ? 'Talking' : 'Talk'}</Label>
            </Pressable>
            <Label style={{ fontSize: 9.5 }}>{prefs.model ? prefs.model.split('/').pop() : 'Default model'}</Label>
          </View>
        </View>
        <Text numberOfLines={2} style={[type.title, { color: c.text, marginTop: space[2], fontSize: 24, lineHeight: 30 }]}>
          {conv?.title || firstLine || (cid ? ' ' : 'New chat')}
        </Text>
      </View>
      <Rule strong style={{ marginHorizontal: space[5] }} />

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={0}>
        <ScrollView
          ref={scroll}
          contentContainerStyle={{ paddingHorizontal: space[5], paddingTop: space[3], paddingBottom: space[6] }}
          onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}
          keyboardDismissMode="interactive"
          keyboardShouldPersistTaps="handled"
        >
          {loadErr ? <Notice text={loadErr} action="Retry" onAction={() => load(cid)} /> : null}
          {!cid && !turn ? <Opening onPick={(s) => setDraft(s)} /> : null}
          {messages.map((m) => (
            <MessageView key={m.id} m={m} />
          ))}
          {turn && pendingText ? <UserBlock text={pendingText} tags={[]} /> : null}
          {turn ? <LiveTurn turn={turn} shown={shown} onDecided={() => load(turn.conversationId)} /> : null}
          {asking && !turn ? (
            /* feat/v02-anywhere-voice: the request stays on screen while it's asked out loud */
            <View style={{ marginTop: space[4], borderTopWidth: hairline, borderTopColor: c.border }}>
              <Label style={{ fontSize: 9.5, marginTop: space[2] }} color={c.accentText}>Say “yes, send it” or “no”, or tap</Label>
              <ApprovalCard
                a={{ id: asking.id, conversation_id: cid, tool: asking.tool, args: asking.args, effect: asking.effect, reason: asking.reason, status: 'pending', result: '', created_at: new Date().toISOString(), decided_at: null }}
                onDecided={() => {
                  setAsking(null);
                  askingRef.current = null;
                  voice.current?.stop();
                  load(cid);
                }}
              />
            </View>
          ) : null}
        </ScrollView>

        {/* Composer */}
        <View style={{ borderTopWidth: 1, borderTopColor: c.rule, backgroundColor: c.paper, paddingHorizontal: space[4], paddingTop: space[2], paddingBottom: Math.max(insets.bottom, space[3]) }}>
          {note ? <T style={{ color: c.muted, fontSize: 13, lineHeight: 18, paddingVertical: 4 }}>{note}</T> : null}
          {files.length ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space[2], paddingVertical: space[2] }}>
              {files.map((f) => (
                <View key={f.key} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, borderWidth: hairline, borderColor: c.border, backgroundColor: c.blueSoft, paddingRight: 8 }}>
                  {f.kind === 'photo' ? <Image source={{ uri: f.uri }} style={{ width: 36, height: 36 }} /> : <View style={{ width: 36, height: 36, alignItems: 'center', justifyContent: 'center' }}><Label>Aud</Label></View>}
                  <Label color={c.text} style={{ fontSize: 9.5 }}>{Math.max(1, Math.round(f.bytes / 1024))} KB</Label>
                  <Pressable onPress={() => setFiles((x) => x.filter((y) => y.key !== f.key))} hitSlop={10} accessibilityLabel="Remove attachment">
                    <Text style={{ color: c.accentText, fontSize: 15 }}>×</Text>
                  </Pressable>
                </View>
              ))}
            </ScrollView>
          ) : null}
          {attachOpen ? (
            <FadeIn from={4} style={{ flexDirection: 'row', gap: space[4], paddingVertical: space[2] }}>
              {Platform.OS !== 'web' ? <AttachOpt label="Camera" onPress={() => attach('camera')} /> : null}
              <AttachOpt label="Photos" onPress={() => attach('library')} />
              <AttachOpt label="Files" onPress={() => attach('file')} />
            </FadeIn>
          ) : null}
          <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: space[2] }}>
            <Pressable onPress={() => setAttachOpen((o) => !o)} accessibilityRole="button" accessibilityLabel="Attach a photo or file" hitSlop={8} style={{ paddingVertical: 10, paddingRight: 4 }}>
              <Text style={{ fontFamily: fonts.displayRegular, fontSize: 26, lineHeight: 26, color: attachOpen ? c.accent : c.text }}>+</Text>
            </Pressable>
            <TextInput
              value={draft}
              onChangeText={setDraft}
              placeholder={listening ? 'Listening…' : 'Write to your computer'}
              placeholderTextColor={listening ? c.accentText : c.faint}
              multiline
              accessibilityLabel="Message"
              style={[type.body, { flex: 1, color: c.text, maxHeight: 140, paddingVertical: 10, paddingHorizontal: 0, fontSize: 16 }]}
            />
            <Pressable onPress={dictate} accessibilityRole="button" accessibilityLabel={listening ? 'Stop dictation' : 'Dictate'} hitSlop={8} style={{ paddingVertical: 12, paddingHorizontal: 6, flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <VoiceOrb on={listening} level={level} size={24} />
              <Label color={listening ? c.accentText : c.text}>{listening ? 'Stop' : 'Voice'}</Label>
            </Pressable>
            <Pressable
              onPress={() => send()}
              disabled={!canSend}
              accessibilityRole="button"
              accessibilityLabel="Send"
              style={({ pressed }) => ({ backgroundColor: canSend ? c.accent : c.panel3, paddingVertical: 11, paddingHorizontal: 14, opacity: pressed ? 0.75 : 1 })}
            >
              <Text style={[type.label, { color: canSend ? c.onAccent : c.muted, fontSize: 11 }]}>Send</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function AttachOpt({ label, onPress }: { label: string; onPress: () => void }) {
  const { colors: c } = useSession();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" style={{ borderBottomWidth: 1, borderBottomColor: c.rule, paddingVertical: 4 }}>
      <Label color={c.text}>{label}</Label>
    </Pressable>
  );
}

function Opening({ onPick }: { onPick: (s: string) => void }) {
  const { colors: c } = useSession();
  const ideas = ['What needs my attention today?', 'Summarise my notes from this week', 'Draft a reply to the last email about the launch'];
  return (
    <FadeIn style={{ paddingTop: space[6] }}>
      <Text style={{ fontFamily: fonts.displayItalic, fontSize: 26, lineHeight: 32, color: c.text }}>What shall we do?</Text>
      <T style={{ color: c.muted, marginTop: space[2] }}>The work happens on your computer, with its files, tools and keys. Anything risky waits for your approval.</T>
      <View style={{ marginTop: space[5] }}>
        {ideas.map((s) => (
          <Pressable key={s} onPress={() => onPick(s)} style={{ paddingVertical: space[3], borderTopWidth: hairline, borderTopColor: c.border, flexDirection: 'row', justifyContent: 'space-between' }}>
            <T style={{ flex: 1 }}>{s}</T>
            <Text style={{ fontFamily: fonts.display, color: c.accent, fontSize: 17 }}>→</Text>
          </Pressable>
        ))}
        <Rule />
      </View>
    </FadeIn>
  );
}

function UserBlock({ text, tags }: { text: string; tags: string[] }) {
  const { colors: c } = useSession();
  return (
    <FadeIn style={{ marginTop: space[5], alignItems: 'flex-end' }}>
      <Label style={{ fontSize: 9.5, marginBottom: 4 }}>You</Label>
      <View style={{ backgroundColor: c.blueSoft, paddingVertical: 10, paddingHorizontal: 12, maxWidth: '88%' }}>
        {text ? <T selectable style={{ fontSize: 15.5 }}>{text}</T> : null}
        {tags.length ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: text ? 6 : 0 }}>
            {tags.map((t, i) => (
              <Label key={i} color={c.text} style={{ fontSize: 9.5, borderWidth: hairline, borderColor: c.rule, paddingHorizontal: 5, paddingVertical: 2 }}>{t}</Label>
            ))}
          </View>
        ) : null}
      </View>
    </FadeIn>
  );
}

function Receipt({ text, tone, live }: { text: string; tone: 'ok' | 'bad' | 'quiet'; live?: boolean }) {
  const { colors: c } = useSession();
  const dot = tone === 'ok' ? c.ok : tone === 'bad' ? c.danger : c.borderStrong;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: space[2] }}>
      {live ? <Node tone={tone} /> : <View style={{ width: 5, height: 5, backgroundColor: dot }} />}
      <Text style={[type.small, { color: c.muted, flex: 1 }]} numberOfLines={1}>{text}</Text>
    </View>
  );
}

function MessageView({ m }: { m: Message }) {
  const { colors: c } = useSession();
  if (m.role === 'tool') return <Receipt text={`Used ${toolName(m.name)}`} tone="quiet" />;
  if (m.role === 'user') {
    const d = decisionNote(m.content);
    if (d) return <Receipt text={`You ${d.approved ? 'approved' : 'denied'} ${toolName(d.tool)} · #${d.id}`} tone={d.approved ? 'ok' : 'bad'} />;
    const { text, tags } = splitAttachmentNotes(m.content);
    return <UserBlock text={text} tags={tags} />;
  }
  if (!m.content) return null;
  return (
    <FadeIn style={{ marginTop: space[5] }}>
      <Label style={{ fontSize: 9.5, marginBottom: 4 }} color={c.accentText}>Workspace</Label>
      <Markdown text={m.content} />
    </FadeIn>
  );
}

function LiveTurn({ turn, shown, onDecided }: { turn: Turn; shown: string; onDecided: () => void }) {
  const { colors: c } = useSession();
  const working = !['done', 'error', 'answered'].includes(turn.phase);
  return (
    <View style={{ marginTop: space[5] }}>
      <Label style={{ fontSize: 9.5, marginBottom: 4 }} color={c.accentText}>Workspace</Label>
      {turn.steps.map((s, i) => (
        <Receipt key={i} live text={`${s.ok === null ? 'Using' : 'Used'} ${toolName(s.tool)}${s.ok === false ? ' · failed' : ''}`} tone={s.ok === null ? 'quiet' : s.ok ? 'ok' : 'bad'} />
      ))}
      {turn.held.map((h) =>
        h.denied ? (
          <Receipt key={h.id} text={`${toolName(h.tool)} was refused by your rules`} tone="bad" />
        ) : (
          <SpringIn key={h.id} style={{ marginTop: space[2], borderTopWidth: hairline, borderTopColor: c.border }}>
            <ApprovalCard
              a={{ id: h.id, conversation_id: turn.conversationId, tool: h.tool, args: h.args, effect: h.effect, reason: h.reason, status: 'pending', result: '', created_at: new Date().toISOString(), decided_at: null }}
              onDecided={onDecided}
            />
          </SpringIn>
        ),
      )}
      {working ? (
        <View style={{ marginTop: space[3] }}>
          <Thinking label={phaseLabel(turn)} />
        </View>
      ) : null}
      {shown ? (
        <SpringIn style={{ marginTop: space[2] }}>
          <Markdown text={shown} />
        </SpringIn>
      ) : null}
      {turn.phase === 'done' && shown && shown === turn.text ? (
        <View style={{ marginTop: space[2], flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <DoneTick size={16} haptic={false} />
          <Label style={{ fontSize: 9.5 }}>Done</Label>
        </View>
      ) : null}
      {turn.phase === 'error' ? (
        <View style={{ marginTop: space[3] }}>
          <Notice text={turn.error || 'Something went wrong on your computer.'} tone="bad" />
        </View>
      ) : null}
    </View>
  );
}
