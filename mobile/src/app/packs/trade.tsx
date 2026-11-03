/** Quotes and invoices on the phone (feat/v02-packs). Reminders are drafts: the share sheet hands one to your own mail app. */
import React, { useState } from 'react';
import { Pressable, ScrollView, Share, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { docStatus, money, packsApi, Reminder, shareText, TradeDoc } from '@/lib/packs';
import { useSession } from '@/lib/session';
import { hairline, space, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { Btn, Empty, FadeIn, Label, Notice, Screen, T, Tag } from '@/components/ui';

export default function Trade() {
  const { colors: c, client } = useSession();
  const docs = useRemote<TradeDoc[]>((cl) => packsApi(cl).docs());
  const [open, setOpen] = useState<TradeDoc | null>(null);
  const [drafts, setDrafts] = useState<{ drafts: Reminder[]; suggested: string; note: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const pick = (d: TradeDoc | null) => { setOpen(d); setDrafts(null); setErr(''); };
  const writeReminders = async () => {
    if (!client || !open) return;
    setBusy(true);
    try { setDrafts(await packsApi(client).reminders(open.id)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }}>
        <Pressable onPress={() => (open ? pick(null) : router.canGoBack() ? router.back() : router.replace('/packs'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  {open ? 'Quotes & invoices' : 'Packs'}</Label>
        </Pressable>
        <Label style={{ marginTop: space[5] }}>Trade pack</Label>
        {docs.error ? <View style={{ marginTop: space[4] }}><Notice text={docs.error} tone="bad" /></View> : null}
        {err ? <View style={{ marginTop: space[4] }}><Notice text={err} tone="bad" /></View> : null}
        {!open ? (
          <>
            <Text style={[type.headline, { color: c.text, marginTop: space[2] }]}>Quotes & invoices</Text>
            <T style={{ color: c.muted, marginTop: space[2] }}>Draft and edit them on your computer. Here you can check one and write a reminder.</T>
            {docs.data && !docs.data.length ? <Empty title="Nothing yet" body="Draft your first quote on your computer (Packs › Trade)." /> : null}
            {(docs.data || []).map((d, i) => {
              const st = docStatus(d);
              return (
                <FadeIn key={d.id} delay={Math.min(i, 8) * 30}>
                  <Pressable onPress={() => { tap(); pick(d); }} accessibilityRole="button"
                    style={{ paddingVertical: space[4], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: space[3] }}>
                      <Text style={[type.bodyMedium, { color: c.text, flex: 1 }]} numberOfLines={1}>{d.number} · {d.customer?.name || 'No customer'}</Text>
                      <Text style={[type.bodyMedium, { color: c.text }]}>{money(d.totals.total)}</Text>
                    </View>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: space[2], marginTop: 4 }}>
                      <Tag label={d.kind === 'quote' ? 'Quote' : 'Invoice'} />
                      <Tag label={st.label} tone={st.tone} />
                      <T style={{ color: c.muted, flex: 1 }} numberOfLines={1}>{d.title}</T>
                    </View>
                  </Pressable>
                </FadeIn>
              );
            })}
          </>
        ) : (
          <FadeIn>
            <Text style={[type.headline, { color: c.text, marginTop: space[2] }]}>{open.number}</Text>
            <T style={{ color: c.muted }}>{open.customer?.name || 'No customer'} · {open.title}{open.due_on ? ` · due ${open.due_on}` : ''}</T>
            <View style={{ marginTop: space[4] }}>
              {(open.items || []).map((it, i) => (
                <View key={i} style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 8, borderBottomWidth: hairline, borderBottomColor: c.border, gap: space[3] }}>
                  <T style={{ flex: 1 }}>{it.description}</T>
                  <T style={{ color: c.muted }}>{it.qty} {it.unit} × {money(it.unit_cost)}</T>
                </View>
              ))}
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: space[3] }}>
                <Text style={[type.title, { color: c.text }]}>Total</Text>
                <Text style={[type.title, { color: c.text }]}>{money(open.totals.total)}</Text>
              </View>
              <T style={{ color: c.muted }}>Prices shown before markup; the total includes markup and GST, as on the PDF.</T>
            </View>
            {open.kind === 'invoice' && open.status !== 'paid' ? (
              <View style={{ marginTop: space[5] }}>
                <Btn label="Write payment reminders" busy={busy} onPress={writeReminders} />
              </View>
            ) : null}
            {drafts ? (
              <View style={{ marginTop: space[4] }}>
                <Notice text={drafts.note} tone="ok" />
                {drafts.drafts.map((r) => (
                  <View key={r.id} style={{ marginTop: space[4], padding: space[4], borderWidth: hairline, borderColor: c.borderStrong }}>
                    <View style={{ flexDirection: 'row', gap: space[2], alignItems: 'center' }}>
                      <Tag label={r.tone === drafts.suggested ? `${r.tone} · suggested` : r.tone} tone={r.tone === drafts.suggested ? 'live' : 'quiet'} />
                      {r.send_on ? <T style={{ color: c.muted, fontSize: 12 }}>send from {r.send_on}</T> : null}
                    </View>
                    <Text style={[type.bodyMedium, { color: c.text, marginTop: space[2] }]}>{r.subject}</Text>
                    <T style={{ marginTop: space[2] }}>{r.body}</T>
                    <View style={{ marginTop: space[3], flexDirection: 'row' }}>
                      <Btn small label="Share to my email" onPress={() => Share.share({ message: shareText(r), title: r.subject }).catch(() => undefined)} />
                    </View>
                  </View>
                ))}
              </View>
            ) : null}
          </FadeIn>
        )}
      </ScrollView>
    </Screen>
  );
}
