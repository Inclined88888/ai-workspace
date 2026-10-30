/**
 * Bottom navigation as a newspaper section strip: tracked uppercase words, the current one underlined in cherry.
 * No icons, no pills.
 */
import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { Redirect } from 'expo-router';
import { Tabs, BottomTabBarProps } from 'expo-router/js-tabs';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useSession } from '@/lib/session';
import { hairline, type } from '@/lib/theme';
import { tap } from '@/lib/haptics';
import { useApprovalsCount } from '@/lib/hooks';

function Bar({ state, navigateToTab, descriptors }: BottomTabBarProps) {
  const { colors: c } = useSession();
  const insets = useSafeAreaInsets();
  const pending = useApprovalsCount();
  return (
    <View style={{ backgroundColor: c.paper, borderTopWidth: hairline, borderTopColor: c.rule, paddingBottom: Math.max(insets.bottom, 10), paddingTop: 2, flexDirection: 'row' }}>
      {state.routes.map((r, i) => {
        const focused = state.index === i;
        const label = (descriptors[r.key].options.title ?? r.name).toUpperCase();
        return (
          <Pressable
            key={r.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: focused }}
            accessibilityLabel={r.name === 'approvals' && pending ? `${label}, ${pending} waiting` : label}
            onPress={() => {
              if (!focused) {
                tap();
                navigateToTab(r.key);
              }
            }}
            style={{ flex: 1, alignItems: 'center', paddingTop: 12, paddingBottom: 6 }}
          >
            <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
              <Text style={[type.label, { fontSize: 9.5, letterSpacing: 1.3, color: focused ? c.text : c.muted }]}>{label}</Text>
              {r.name === 'approvals' && pending > 0 ? (
                <Text style={[type.label, { fontSize: 8.5, color: c.accent, marginLeft: 2, marginTop: -3 }]}>{pending}</Text>
              ) : null}
            </View>
            <View style={{ height: 2, width: 18, marginTop: 6, backgroundColor: focused ? c.accent : 'transparent' }} />
          </Pressable>
        );
      })}
    </View>
  );
}

export default function TabsLayout() {
  const { status, colors } = useSession();
  if (status === 'loading') return null;
  if (status === 'unpaired') return <Redirect href="/pair" />;
  return (
    <Tabs tabBar={(p) => <Bar {...p} />} screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: colors.paper }, animation: 'fade' }}>
      <Tabs.Screen name="index" options={{ title: 'Today' }} />
      <Tabs.Screen name="chats" options={{ title: 'Chats' }} />
      <Tabs.Screen name="approvals" options={{ title: 'Approvals' }} />
      <Tabs.Screen name="runs" options={{ title: 'Runs' }} />
      <Tabs.Screen name="settings" options={{ title: 'Settings' }} />
    </Tabs>
  );
}
