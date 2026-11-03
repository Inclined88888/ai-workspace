/** Flashcard review on the phone (feat/v02-packs): tap to turn the card, then say how well you knew it. */
import React, { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useRemote } from '@/lib/hooks';
import { Card, Deck, Grade, GRADES, packsApi } from '@/lib/packs';
import { useSession } from '@/lib/session';
import { hairline, space, type } from '@/lib/theme';
import { success, tap } from '@/lib/haptics';
import { Btn, Empty, FadeIn, Label, Notice, Screen, T } from '@/components/ui';

export default function Study() {
  const { colors: c, client } = useSession();
  const [deck, setDeck] = useState<string | null>(null);
  const decks = useRemote<Deck[]>((cl) => packsApi(cl).decks());
  const queue = useRemote<Card[]>((cl) => (deck === null ? Promise.resolve([]) : packsApi(cl).due(deck)), 0, deck ?? '');
  const [flipped, setFlipped] = useState(false);
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const card = queue.data?.[0];

  const grade = async (g: Grade) => {
    if (!client || !card) return;
    try {
      const r = await packsApi(client).review(card.id, g);
      success();
      setNote(`Next time: ${r.next_in}`);
      setFlipped(false);
      queue.set((queue.data || []).slice(1));
      if ((queue.data || []).length <= 1) decks.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space[5], paddingBottom: space[12] }}>
        <Pressable onPress={() => (deck !== null ? setDeck(null) : router.canGoBack() ? router.back() : router.replace('/packs'))} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Label color={c.text}>‹  {deck !== null ? 'Decks' : 'Packs'}</Label>
        </Pressable>
        <Label style={{ marginTop: space[5] }}>Study pack</Label>
        <Text style={[type.headline, { color: c.text, marginTop: space[2] }]}>{deck === null ? 'Flashcards' : deck || 'All decks'}</Text>
        {decks.error ? <View style={{ marginTop: space[4] }}><Notice text={decks.error} tone="bad" /></View> : null}
        {err ? <View style={{ marginTop: space[4] }}><Notice text={err} tone="bad" /></View> : null}
        {deck === null ? (
          <View style={{ marginTop: space[4] }}>
            {decks.data && !decks.data.length ? <Empty title="No cards yet" body="Make flashcards from a file on your computer (Packs › Study)." /> : null}
            {decks.data && decks.data.length > 1 ? (
              <Pressable onPress={() => { tap(); setDeck(''); }} style={{ paddingVertical: space[4], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                <Text style={[type.title, { color: c.text }]}>Everything due</Text>
                <T style={{ color: c.muted }}>{decks.data.reduce((n, d) => n + (d.due || 0), 0)} due</T>
              </Pressable>
            ) : null}
            {(decks.data || []).map((d) => (
              <Pressable key={d.deck} onPress={() => { tap(); setDeck(d.deck); }} accessibilityRole="button"
                style={{ paddingVertical: space[4], borderBottomWidth: hairline, borderBottomColor: c.border }}>
                <Text style={[type.title, { color: c.text }]}>{d.deck}</Text>
                <T style={{ color: c.muted }}>{d.due || 0} due · {d.cards} cards</T>
              </Pressable>
            ))}
          </View>
        ) : !card ? (
          <View style={{ marginTop: space[6] }}>
            <Empty title={queue.loading ? 'Loading…' : 'All done for now'} body={note || 'Nothing else is due in this deck.'} />
          </View>
        ) : (
          <FadeIn key={`${card.id}-${flipped}`}>
            <Pressable onPress={() => { tap(); setFlipped(!flipped); }} accessibilityRole="button" accessibilityLabel={flipped ? 'Show question' : 'Show answer'}
              style={{ marginTop: space[5], minHeight: 240, padding: space[6], borderWidth: 1, borderColor: c.rule, backgroundColor: c.panel, justifyContent: 'center', alignItems: 'center' }}>
              <Label>{flipped ? 'Answer' : 'Question'}</Label>
              <Text style={[flipped ? type.body : type.title, { color: c.text, textAlign: 'center', marginTop: space[3] }]}>{flipped ? card.back : card.front}</Text>
              {flipped && card.source ? <Text style={[type.small, { color: c.faint, marginTop: space[4] }]}>{card.source}</Text> : null}
              {!flipped ? <Text style={[type.small, { color: c.muted, marginTop: space[4] }]}>Tap to see the answer</Text> : null}
            </Pressable>
            <T style={{ color: c.muted, marginTop: space[3] }}>{(queue.data || []).length} to go{note ? `  ·  ${note}` : ''}</T>
            {flipped ? (
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space[2], marginTop: space[4] }}>
                {GRADES.map((g) => <Btn key={g.grade} small label={g.label} kind={g.grade === 'good' ? 'accent' : 'outline'} onPress={() => grade(g.grade)} accessibilityHint={g.hint} />)}
              </View>
            ) : null}
          </FadeIn>
        )}
      </ScrollView>
    </Screen>
  );
}
