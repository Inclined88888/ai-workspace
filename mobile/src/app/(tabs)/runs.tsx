/** Runs and receipts: every turn, job and tool call, with live status. Filter to failures; load older pages. */
import React, { useState } from 'react';
import { FlatList, Pressable, RefreshControl, View } from 'react-native';
import { RunCard } from '@/lib/api';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { space } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { RunRow } from '@/components/RunRow';
import { ConnectionMark, OfflineNotice } from '@/components/Connection';
import { Btn, Empty, FadeIn, Label, Masthead, Notice, Screen } from '@/components/ui';
import { SkeletonRows } from '@/components/motion';

export default function Runs() {
  const { colors: c, client } = useSession();
  const [failed, setFailed] = useState(false);
  const [older, setOlder] = useState<RunCard[]>([]);
  const [more, setMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const list = useRemote<RunCard[]>(async (cl) => (await cl.runs(undefined, failed)).runs, 5000, failed ? 'failed' : 'all');
  const head = list.data ?? [];
  const seen = new Set(head.map((r) => r.id));
  const rows = [...head, ...older.filter((r) => !seen.has(r.id))];
  const running = head.filter((r) => r.outcome === 'running').length;

  const loadMore = async () => {
    if (!client || !rows.length || loadingMore) return;
    setLoadingMore(true);
    try {
      const r = (await client.runs(rows[rows.length - 1].id, failed)).runs;
      setOlder((o) => [...o, ...r]);
      setMore(r.length >= 30);
    } finally {
      setLoadingMore(false);
    }
  };


  const pick = (value: boolean) => {
    tap();
    setOlder([]);
    setMore(true);
    setFailed(value);
  };

  return (
    <Screen>
      <FlatList
        data={rows}
        keyExtractor={(r) => String(r.id)}
        refreshControl={<RefreshControl refreshing={list.refreshing} onRefresh={list.refresh} tintColor={c.accent} />}
        contentContainerStyle={{ paddingBottom: space[12] }}
        ListHeaderComponent={
          <View>
            <Masthead
              kicker={running ? `${running} running now` : 'Receipts'}
              title="Runs"
              deck="Every job your computer did, what it cost, and whether it checked out."
              right={<ConnectionMark />}
            />
            <OfflineNotice />
            {list.error ? (
              <View style={{ paddingHorizontal: space[5], paddingTop: space[3] }}>
                <Notice text={list.error} />
              </View>
            ) : null}
            <View style={{ flexDirection: 'row', paddingHorizontal: space[5], paddingTop: space[4] }}>
              <Filter on={!failed} label="All" onPick={() => pick(false)} />
              <Filter on={failed} label="Needs a look" onPick={() => pick(true)} />
            </View>
          </View>
        }
        ListEmptyComponent={list.loading ? <SkeletonRows /> : <Empty title={failed ? 'Nothing failed.' : 'No runs yet.'} body={failed ? 'Every recent run finished cleanly.' : 'Ask your computer something and its receipt appears here.'} />}
        renderItem={({ item, index }) => (
          <FadeIn delay={Math.min(index, 6) * 25} style={{ paddingHorizontal: space[5] }}>
            <RunRow r={item} />
          </FadeIn>
        )}
        ListFooterComponent={
          rows.length >= 30 && more ? (
            <View style={{ padding: space[5] }}>
              <Btn label="Older" kind="outline" small busy={loadingMore} onPress={loadMore} />
            </View>
          ) : undefined
        }
      />
    </Screen>
  );
}

function Filter({ on, label, onPick }: { on: boolean; label: string; onPick: () => void }) {
  const { colors: c } = useSession();
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected: on }}
      onPress={onPick}
      style={{ paddingVertical: 6, marginRight: space[5], borderBottomWidth: 2, borderBottomColor: on ? c.accent : 'transparent' }}
    >
      <Label color={on ? c.text : c.muted}>{label}</Label>
    </Pressable>
  );
}
