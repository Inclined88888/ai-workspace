/** Conversations from every surface (desktop, terminal, phone, Telegram), newest first. */
import React from 'react';
import { FlatList, Pressable, RefreshControl, Text, View } from 'react-native';
import { router } from 'expo-router';
import { Conversation } from '@/lib/api';
import { useRemote } from '@/lib/hooks';
import { useSession } from '@/lib/session';
import { relativeTime } from '@/lib/format';
import { hairline, space, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { OfflineNotice, ConnectionMark } from '@/components/Connection';
import { Btn, Empty, FadeIn, Label, Masthead, Notice, Screen } from '@/components/ui';

const FROM: Record<string, string> = { web: 'Desktop', cli: 'Terminal', mobile: 'Phone', telegram: 'Telegram' };

export default function Chats() {
  const { colors: c } = useSession();
  const list = useRemote<Conversation[]>((cl) => cl.conversations(), 15000);
  const open = (id: number | 'new') => {
    tap();
    router.push({ pathname: '/chat/[id]', params: { id: String(id) } });
  };
  return (
    <Screen>
      <FlatList
        data={list.data ?? []}
        keyExtractor={(x) => String(x.id)}
        refreshControl={<RefreshControl refreshing={list.refreshing} onRefresh={list.refresh} tintColor={c.accent} />}
        contentContainerStyle={{ paddingBottom: space[12] }}
        ListHeaderComponent={
          <View>
            <Masthead kicker="Correspondence" title="Chats" deck="Every conversation, wherever you started it." right={<ConnectionMark />} />
            <OfflineNotice />
            {list.error ? (
              <View style={{ paddingHorizontal: space[5], paddingTop: space[3] }}>
                <Notice text={list.error} />
              </View>
            ) : null}
            <View style={{ paddingHorizontal: space[5], paddingTop: space[4], paddingBottom: space[2] }}>
              <Btn label="New chat" kind="ink" onPress={() => open('new')} />
            </View>
          </View>
        }
        ListEmptyComponent={list.loading ? undefined : <Empty title="No chats yet." body="Start one here or on your computer; they all show up in this list." />}
        renderItem={({ item, index }) => (
          <FadeIn delay={Math.min(index, 6) * 30}>
            <Pressable
              onPress={() => open(item.id)}
              accessibilityRole="button"
              accessibilityLabel={`${item.title}, from ${FROM[item.channel] ?? item.channel}, ${relativeTime(item.updated_at)}`}
              style={({ pressed }) => ({
                marginHorizontal: space[5],
                paddingVertical: space[4],
                borderBottomWidth: hairline,
                borderBottomColor: c.border,
                opacity: pressed ? 0.6 : 1,
              })}
            >
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Label style={{ fontSize: 9.5 }} color={item.channel === 'mobile' ? c.accentText : c.muted}>{FROM[item.channel] ?? item.channel}</Label>
                <Label style={{ fontSize: 9.5 }}>{relativeTime(item.updated_at)}</Label>
              </View>
              <Text numberOfLines={2} style={[type.title, { color: c.text, fontSize: 20, lineHeight: 26, marginTop: 4 }]}>{item.title}</Text>
            </Pressable>
          </FadeIn>
        )}
      />
    </Screen>
  );
}
