/**
 * Push notifications: approval needed, job finished. The phone asks permission, gets an Expo push token and gives it
 * to the desktop (PUT /api/mobile/push-token). The desktop sends through Expo's push service, which hands off to
 * APNs (iOS) and FCM (Android). See mobile/README.md -> "Push notifications in production".
 */
import { Platform } from 'react-native';
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';

export type PushTarget = { kind: 'approval' | 'run'; id?: number; conversation_id?: number };

Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: true }),
});

export async function registerForPush(): Promise<{ token: string | null; reason: string }> {
  if (Platform.OS === 'web') return { token: null, reason: 'Notifications are for the phone app.' };
  if (!Device.isDevice) return { token: null, reason: 'Push needs a real phone, not a simulator.' };
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'Approvals and jobs',
      // eslint-disable-next-line import/namespace -- re-exported enum the import plugin cannot follow
      importance: Notifications.AndroidImportance.HIGH,
      lightColor: '#D21F3C',
    });
  }
  const existing = await Notifications.getPermissionsAsync();
  let granted = existing.granted;
  if (!granted) granted = (await Notifications.requestPermissionsAsync()).granted;
  if (!granted) return { token: null, reason: 'Notifications are off for this app in Settings.' };
  const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  if (!projectId) return { token: null, reason: 'This build has no EAS project id yet (run `eas init`).' };
  try {
    const t = await Notifications.getExpoPushTokenAsync({ projectId });
    return { token: t.data, reason: '' };
  } catch (e) {
    return { token: null, reason: e instanceof Error ? e.message : 'Could not get a push token.' };
  }
}

/** Where a tapped notification should open. */
export function routeFor(data: Record<string, unknown> | undefined): string | null {
  if (!data) return null;
  if (data.kind === 'approval') return '/approvals';
  if (data.kind === 'job') return '/jobs'; // feat/v02-automations: jobs board
  if (data.kind === 'run') {
    if (typeof data.conversation_id === 'number') return `/chat/${data.conversation_id}`;
    if (typeof data.id === 'number') return `/run/${data.id}`;
    return '/runs';
  }
  return null;
}
