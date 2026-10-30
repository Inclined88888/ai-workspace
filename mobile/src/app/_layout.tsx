import React, { useEffect } from 'react';
import { Platform } from 'react-native';
import { Stack, router } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import * as SplashScreen from 'expo-splash-screen';
import * as SystemUI from 'expo-system-ui';
import * as Notifications from 'expo-notifications';
import { useFonts } from 'expo-font';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { BodoniModa_400Regular } from '@expo-google-fonts/bodoni-moda/400Regular';
import { BodoniModa_400Regular_Italic } from '@expo-google-fonts/bodoni-moda/400Regular_Italic';
import { BodoniModa_500Medium } from '@expo-google-fonts/bodoni-moda/500Medium';
import { HankenGrotesk_400Regular } from '@expo-google-fonts/hanken-grotesk/400Regular';
import { HankenGrotesk_500Medium } from '@expo-google-fonts/hanken-grotesk/500Medium';
import { HankenGrotesk_600SemiBold } from '@expo-google-fonts/hanken-grotesk/600SemiBold';
import { SessionProvider, useSession } from '@/lib/session';
import { routeFor } from '@/lib/push';

SplashScreen.preventAutoHideAsync().catch(() => undefined);

function Shell() {
  const { colors, dark, status } = useSession();

  useEffect(() => {
    SystemUI.setBackgroundColorAsync(colors.paper).catch(() => undefined);
  }, [colors.paper]);

  useEffect(() => {
    if (status !== 'loading') SplashScreen.hideAsync().catch(() => undefined);
  }, [status]);

  // A tapped notification opens the thing it is about.
  useEffect(() => {
    if (Platform.OS === 'web') return;
    const open = (r: Notifications.NotificationResponse | null) => {
      const to = routeFor(r?.notification.request.content.data as Record<string, unknown> | undefined);
      if (to) router.push(to as never);
    };
    Notifications.getLastNotificationResponseAsync().then(open).catch(() => undefined);
    const sub = Notifications.addNotificationResponseReceivedListener(open);
    return () => sub.remove();
  }, []);

  return (
    <>
      <StatusBar style={dark ? 'light' : 'dark'} />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.paper },
          animation: 'fade',
          animationDuration: 200,
        }}
      >
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="pair" options={{ animation: 'fade' }} />
        <Stack.Screen name="scan" options={{ presentation: 'fullScreenModal', animation: 'fade' }} />
        <Stack.Screen name="chat/[id]" options={{ animation: 'slide_from_right' }} />
        <Stack.Screen name="run/[id]" options={{ animation: 'slide_from_right' }} />
        <Stack.Screen name="jobs" options={{ animation: 'slide_from_right' }} />{/* feat/v02-automations */}
      </Stack>
    </>
  );
}

export default function RootLayout() {
  const [loaded] = useFonts({
    BodoniModa_400Regular,
    BodoniModa_400Regular_Italic,
    BodoniModa_500Medium,
    HankenGrotesk_400Regular,
    HankenGrotesk_500Medium,
    HankenGrotesk_600SemiBold,
  });
  if (!loaded) return null;
  return (
    <SafeAreaProvider>
      <SessionProvider>
        <Shell />
      </SessionProvider>
    </SafeAreaProvider>
  );
}
