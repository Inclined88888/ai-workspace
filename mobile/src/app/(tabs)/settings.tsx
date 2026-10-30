/** Settings: the paired computer, model, look, notifications, and unpairing. */
import React, { useState } from 'react';
import { Alert, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import Constants from 'expo-constants';
import { router } from 'expo-router';
import { Me, Model } from '@/lib/api';
import { useRemote } from '@/lib/hooks';
import { modelName } from '@/lib/format';
import { useSession } from '@/lib/session';
import { Prefs } from '@/lib/storage';
import { registerForPush } from '@/lib/push';
import { fonts, hairline, Palette, palettes, space, themeLabels, type } from '@/lib/theme';
import { success, tap, warning } from '@/lib/haptics';
import { ConnectionMark } from '@/components/Connection';
import { Btn, Label, Masthead, Notice, Screen, Section, T } from '@/components/ui';

const MOTION_LABEL = { full: 'Full', calm: 'Calm', off: 'Off' } as const;
const MOTION_NEXT = { full: 'calm', calm: 'off', off: 'full' } as const;

function Row({ k, v, onPress, mono }: { k: string; v: string; onPress?: () => void; mono?: boolean }) {
  const { colors: c } = useSession();
  return (
    <Pressable disabled={!onPress} onPress={onPress} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 13, borderBottomWidth: hairline, borderBottomColor: c.border, gap: space[4] }}>
      <T style={{ color: c.muted }}>{k}</T>
      <Text numberOfLines={1} style={[type.body, { color: c.text, flexShrink: 1, textAlign: 'right' }, mono ? { fontFamily: fonts.mono, fontSize: 13 } : null]}>{v}</Text>
    </Pressable>
  );
}

/** The same six themes as the computer, each drawn in its own colours, plus "Same as computer" and "Phone setting". */
function ThemeList({ value, onChange }: { value: Prefs['theme']; onChange: (v: Prefs['theme']) => void }) {
  const { colors: c } = useSession();
  const current = value === 'light' ? 'paper' : value === 'dark' ? 'night' : value;
  const rows: [Prefs['theme'], string, Palette | null][] = [
    ['computer', 'Same as computer', null],
    ...themeLabels.map(([id, label]) => [id, label, palettes[id]] as [Prefs['theme'], string, Palette]),
    ['system', 'Match this phone (Sunshine or Dark)', null],
  ];
  return (
    <View style={{ marginTop: space[3] }} accessibilityRole="radiogroup">
      {rows.map(([id, label, p]) => {
        const on = id === current;
        return (
          <Pressable key={id} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={() => { tap(); onChange(id); }}
            style={{ flexDirection: 'row', alignItems: 'center', gap: space[3], paddingVertical: 11, borderBottomWidth: hairline, borderBottomColor: c.border }}>
            <View style={{ width: 34, height: 22, borderWidth: 1, borderColor: p ? p.border : c.rule, backgroundColor: p ? p.paper : c.panel2, flexDirection: 'row', alignItems: 'flex-end', padding: 3 }}>
              {p ? <View style={{ width: 12, height: 6, backgroundColor: p.accent }} /> : null}
            </View>
            <Text style={[type.body, { flex: 1, color: c.text }]}>{label}</Text>
            <View style={{ width: 12, height: 12, borderWidth: 1, borderColor: c.rule, backgroundColor: on ? c.accent : 'transparent' }} />
          </Pressable>
        );
      })}
    </View>
  );
}

export default function Settings() {
  const { colors: c, pairing, client, prefs, setPrefs, forget, status } = useSession();
  const me = useRemote<Me>((cl) => cl.me());
  const models = useRemote<{ models: Model[]; default: string } | null>((cl) => cl.models().catch(() => null));
  const [pushMsg, setPushMsg] = useState('');
  const [pushBusy, setPushBusy] = useState(false);

  const enablePush = async () => {
    setPushBusy(true);
    setPushMsg('');
    const r = await registerForPush();
    if (r.token && client) {
      try {
        const res = await client.pushToken(r.token);
        success();
        setPushMsg(res.push_enabled_on_desktop ? 'On. You’ll hear when something needs you or a job finishes.' : 'Saved. Turn on “Phone notifications” in your computer’s Settings › Phone to start them.');
        me.reload();
      } catch (e) {
        setPushMsg(e instanceof Error ? e.message : String(e));
      }
    } else setPushMsg(r.reason);
    setPushBusy(false);
  };

  const unpair = () => {
    const go = async () => {
      warning();
      try {
        await client?.unpair();
      } catch {
        /* offline: forget locally; the computer can revoke it from Settings › Phone */
      }
      await forget();
      router.replace('/pair');
    };
    if (Platform.OS === 'web') return void go();
    Alert.alert('Unpair this phone?', 'Its key is deleted here and revoked on your computer. You can pair again any time.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Unpair', style: 'destructive', onPress: go },
    ]);
  };

  const list = models.data?.models ?? [];
  const def = models.data?.default ?? '';

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ paddingBottom: space[12] }}>
        <Masthead kicker="The small print" title="Settings" right={<ConnectionMark />} />
        <View style={{ paddingHorizontal: space[5] }}>
          <Section title="Paired computer" />
          <Text style={[type.title, { color: c.text, marginTop: space[3] }]}>{me.data?.workspace_name || pairing?.name || 'AI Workspace'}</Text>
          <Row k="Address" v={(pairing?.active || '').replace(/^https?:\/\//, '')} mono />
          {pairing && pairing.urls.length > 1 ? <Row k="Also tries" v={`${pairing.urls.length - 1} more`} /> : null}
          <Row k="This phone" v={me.data?.name || '—'} />
          <Row k="Status" v={status === 'online' ? 'Connected' : status === 'connecting' ? 'Connecting' : 'Not reachable'} />

          <Section title="Model" />
          {models.data === null && !models.loading ? (
            <T style={{ color: c.muted, paddingVertical: space[3] }}>Your computer chooses the model. Update AI Workspace on the computer to pick one here.</T>
          ) : (
            <View>
              {[{ ref: '', name: def ? `Computer’s default · ${modelName(def)}` : 'Computer’s default' } as Model, ...list].map((m) => {
                const on = prefs.model === m.ref;
                return (
                  <Pressable
                    key={m.ref || 'default'}
                    accessibilityRole="radio"
                    accessibilityState={{ selected: on }}
                    onPress={() => {
                      tap();
                      setPrefs({ model: m.ref });
                    }}
                    style={{ flexDirection: 'row', alignItems: 'center', gap: space[3], paddingVertical: 12, borderBottomWidth: hairline, borderBottomColor: c.border }}
                  >
                    <View style={{ width: 12, height: 12, borderWidth: 1, borderColor: c.rule, backgroundColor: on ? c.accent : 'transparent' }} />
                    <View style={{ flex: 1 }}>
                      <T numberOfLines={1}>{m.name || m.model || m.ref}</T>
                      {m.ref ? <Text style={[type.small, { color: c.muted, fontSize: 12 }]} numberOfLines={1}>{[m.provider, m.free ? 'free' : ''].filter(Boolean).join('  ·  ') || m.ref}</Text> : null}
                    </View>
                  </Pressable>
                );
              })}
            </View>
          )}

          <Section title="Appearance" />
          <ThemeList value={prefs.theme} onChange={(theme) => setPrefs({ theme })} />
          <Pressable
            accessibilityRole="switch"
            accessibilityState={{ checked: prefs.haptics }}
            onPress={() => setPrefs({ haptics: !prefs.haptics })}
            style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 14, borderBottomWidth: hairline, borderBottomColor: c.border, marginTop: space[2] }}
          >
            <T>Haptics</T>
            <Label color={prefs.haptics ? c.text : c.muted}>{prefs.haptics ? 'On' : 'Off'}</Label>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Motion: ${MOTION_LABEL[prefs.motion ?? 'full']}. Tap to change.`}
            onPress={() => {
              tap();
              setPrefs({ motion: MOTION_NEXT[prefs.motion ?? 'full'] });
            }}
            style={{ paddingVertical: 14, borderBottomWidth: hairline, borderBottomColor: c.border }}
          >
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <T>Motion</T>
              <Label color={c.text}>{MOTION_LABEL[prefs.motion ?? 'full']}</Label>
            </View>
            <T style={{ color: c.muted, fontSize: 13, marginTop: 4 }}>
              {prefs.motion === 'calm' ? 'Calm: gentle fades, nothing bounces or slides.' : prefs.motion === 'off' ? 'Nothing moves.' : 'Cards settle on a spring, the orb follows your voice.'}{" Your phone’s Reduce Motion setting always wins."}
            </T>
          </Pressable>

          <Section title="Notifications" />
          <T style={{ color: c.muted, marginTop: space[3] }}>
            {me.data?.push ? 'This phone gets a notice when an approval is waiting and when a job finishes.' : 'Get a notice when an approval is waiting and when a job finishes.'}
          </T>
          {me.data && !me.data.push_enabled_on_desktop ? (
            <View style={{ marginTop: space[3] }}>
              <Notice text="Phone notifications are off on your computer (Settings › Phone)." />
            </View>
          ) : null}
          <Btn label={me.data?.push ? 'Refresh notifications' : 'Turn on notifications'} kind="blue" style={{ marginTop: space[3] }} busy={pushBusy} onPress={enablePush} />
          {pushMsg ? <T style={{ marginTop: space[2], fontSize: 13.5 }}>{pushMsg}</T> : null}

          <Section title="Privacy" />
          <T style={{ color: c.muted, marginTop: space[3], fontSize: 14, lineHeight: 21 }}>
            This phone holds one revocable key, in the {Platform.OS === 'android' ? 'Android Keystore' : 'iOS Keychain'}. No model keys,
            passwords or files are stored here. Everything it approves, denies or asks is written to your computer’s receipts,
            marked “phone”.
          </T>
          <Btn label="Unpair this phone" kind="outline" style={{ marginTop: space[5], borderColor: c.accent }} onPress={unpair} />
          <Label style={{ marginTop: space[6], textAlign: 'center' }}>AI Workspace · {Constants.expoConfig?.version ?? '0.1.0'}</Label>
        </View>
      </ScrollView>
    </Screen>
  );
}
