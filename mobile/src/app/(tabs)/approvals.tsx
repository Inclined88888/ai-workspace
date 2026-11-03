/** Approvals inbox: every held action, one tap to approve or deny. Polls while open. */
import React from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import { Approval } from '@/lib/api';
import { setApprovalsCount, useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { space } from '@/lib/theme';
import { ApprovalCard } from '@/components/ApprovalCard';
import { ConnectionMark, OfflineNotice } from '@/components/Connection';
import { Empty, FadeIn, Masthead, Notice, Screen } from '@/components/ui';

export default function Approvals() {
  const { colors: c } = useSession();
  const list = useRemote<Approval[]>(async (cl) => {
    const a = await cl.approvals();
    setApprovalsCount(a.length);
    return a;
  }, 5000);
  const items = list.data ?? [];
  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ paddingBottom: space[12] }}
        refreshControl={<RefreshControl refreshing={list.refreshing} onRefresh={list.refresh} tintColor={c.accent} />}
      >
        <Masthead
          kicker={items.length ? `${items.length} waiting` : 'In tray'}
          title="Approvals"
          deck="Anything that writes, sends or schedules waits here for your word."
          right={<ConnectionMark />}
        />
        <OfflineNotice />
        {list.error ? (
          <View style={{ paddingHorizontal: space[5], paddingTop: space[3] }}>
            <Notice text={list.error} />
          </View>
        ) : null}
        {!list.loading && !items.length ? (
          <Empty title="Nothing waiting." body="When your computer wants to do something that needs your yes, it lands here and on your lock screen." />
        ) : (
          <View style={{ paddingHorizontal: space[5] }}>
            {items.map((a, i) => (
              <FadeIn key={a.id} delay={Math.min(i, 5) * 40}>
                <ApprovalCard
                  a={a}
                  onDecided={() => {
                    const rest = items.filter((x) => x.id !== a.id);
                    list.set(rest);
                    setApprovalsCount(rest.length);
                  }}
                />
              </FadeIn>
            ))}
          </View>
        )}
      </ScrollView>
    </Screen>
  );
}
