/**
 * First run: pair this phone with AI Workspace on a computer. Scan the QR in the desktop's
 * Settings > Phone card, or type the address and the 8-letter code. A QR deep link
 * (aiworkspace://pair?c=...&u=...) lands here with the fields filled and pairs straight away.
 */
import React, { useEffect, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, Text, TextInput, View } from 'react-native';
import { Redirect, router, useLocalSearchParams } from 'expo-router';
import * as Device from 'expo-device';
import { useSession } from '@/lib/session';
import { formatCode, isCompleteCode, normaliseAddress, normaliseCode } from '@/lib/pairing';
import { fonts, hairline, space, type } from '@/lib/theme';
import { error as hError, success } from '@/lib/haptics';
import { Btn, FadeIn, Label, Notice, Rule, Screen, T } from '@/components/ui';

function deviceName(): string {
  return (Device.deviceName || Device.modelName || (Platform.OS === 'ios' ? 'iPhone' : Platform.OS === 'android' ? 'Android phone' : 'Browser')).slice(0, 60);
}

export default function Pair() {
  const { colors: c, status, pair } = useSession();
  const params = useLocalSearchParams<{ c?: string; u?: string | string[]; n?: string }>();
  const [code, setCode] = useState('');
  const [addr, setAddr] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const auto = useRef(false);

  const submit = async (rawCode: string, urls: string[]) => {
    setErr('');
    if (!isCompleteCode(rawCode)) return setErr('The code is 8 letters and numbers, like ABCD-EFGH.');
    if (!urls.length) return setErr('Type the address shown under the QR code, like 192.168.1.20:8766.');
    setBusy(true);
    try {
      await pair(normaliseCode(rawCode), urls, deviceName());
      success();
      router.replace('/');
    } catch (e) {
      hError();
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (auto.current || !params.c) return;
    const urls = ([] as string[]).concat(params.u ?? []).map((u) => normaliseAddress(u)).filter((u): u is string => !!u);
    // A deep link (aiworkspace://pair?c=…&u=…) pre-fills the form once.
    /* eslint-disable react-hooks/set-state-in-effect */
    setCode(formatCode(params.c));
    if (urls[0]) setAddr(urls[0].replace(/^http:\/\//, ''));
    /* eslint-enable react-hooks/set-state-in-effect */
    auto.current = true;
    if (urls.length) submit(params.c, urls);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.c]);

  if (status === 'online' || status === 'offline' || status === 'connecting') {
    if (!busy && !params.c) return <Redirect href="/" />;
  }

  const input = {
    borderBottomWidth: 1,
    borderBottomColor: c.rule,
    paddingVertical: 10,
    color: c.text,
  };

  return (
    <Screen>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }} keyboardShouldPersistTaps="handled">
          <FadeIn>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text style={[type.masthead, { color: c.text }]}>AI Workspace</Text>
              <Label>Companion</Label>
            </View>
            <Rule strong style={{ marginTop: space[2] }} />
            <Text style={{ fontFamily: fonts.display, fontSize: 44, lineHeight: 48, color: c.text, marginTop: space[8], letterSpacing: -0.6 }}>
              Your computer,{'\n'}
              <Text style={{ fontFamily: fonts.displayItalic, color: c.accent }}>in your pocket.</Text>
            </Text>
            <T style={{ color: c.muted, marginTop: space[4] }}>
              The phone is the remote. Chats, approvals and runs happen on your computer; this app reads them and answers
              for you, over your own Wi-Fi.
            </T>
          </FadeIn>

          <FadeIn delay={80} style={{ marginTop: space[8] }}>
            {[
              ['01', 'On your computer, open AI Workspace › Settings › Phone.'],
              ['02', 'Press “Pair a phone”. A code appears for five minutes.'],
              ['03', 'Scan it here, or type the address and code below.'],
            ].map(([n, t]) => (
              <View key={n} style={{ flexDirection: 'row', paddingVertical: space[3], borderTopWidth: hairline, borderTopColor: c.border, gap: space[4] }}>
                <Text style={{ fontFamily: fonts.displayItalic, fontSize: 18, color: c.accent, width: 26 }}>{n}</Text>
                <T style={{ flex: 1 }}>{t}</T>
              </View>
            ))}
            <Rule />
          </FadeIn>

          <FadeIn delay={140} style={{ marginTop: space[6] }}>
            <Btn label="Scan the code" kind="ink" onPress={() => router.push('/scan')} disabled={busy || Platform.OS === 'web'} />
            {Platform.OS === 'web' ? <Label style={{ marginTop: space[2] }}>Scanning needs the phone app · type the code instead</Label> : null}
          </FadeIn>

          <FadeIn delay={200} style={{ marginTop: space[8] }}>
            <Label color={c.text}>Or type it</Label>
            <View style={{ marginTop: space[4] }}>
              <Label>Computer address</Label>
              <TextInput
                value={addr}
                onChangeText={setAddr}
                placeholder="192.168.1.20:8766"
                placeholderTextColor={c.faint}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                accessibilityLabel="Computer address"
                style={[type.body, input, { fontSize: 17 }]}
              />
            </View>
            <View style={{ marginTop: space[5] }}>
              <Label>Pairing code</Label>
              <TextInput
                value={code}
                onChangeText={(t) => setCode(formatCode(t))}
                placeholder="ABCD-EFGH"
                placeholderTextColor={c.faint}
                autoCapitalize="characters"
                autoCorrect={false}
                maxLength={9}
                accessibilityLabel="Pairing code"
                style={[input, { fontFamily: fonts.display, fontSize: 30, letterSpacing: 4 }]}
              />
            </View>
            {err ? (
              <View style={{ marginTop: space[4] }}>
                <Notice text={err} tone="bad" />
              </View>
            ) : null}
            <Btn
              label={busy ? 'Pairing' : 'Pair this phone'}
              kind="accent"
              busy={busy}
              style={{ marginTop: space[5] }}
              disabled={busy}
              onPress={() => {
                const u = normaliseAddress(addr);
                submit(code, u ? [u] : []);
              }}
            />
            <T style={{ color: c.muted, fontSize: 13, lineHeight: 19, marginTop: space[4] }}>
              Pairing gives this phone its own key, kept in the phone’s secure storage. You can unpair it from either
              side at any time, and everything it does is logged on your computer.
            </T>
          </FadeIn>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}
