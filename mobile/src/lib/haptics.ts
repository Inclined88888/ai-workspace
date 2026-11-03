import { Platform } from 'react-native';
import * as Haptics from 'expo-haptics';

let enabled = true;
export const setHaptics = (on: boolean) => {
  enabled = on;
};

const run = (fn: () => Promise<void>) => {
  if (!enabled || Platform.OS === 'web') return;
  fn().catch(() => undefined);
};

/** A light tick for taps that change something (send, tab, toggle). */
export const tap = () => run(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light));
/** Approve, paired, job done. */
export const success = () => run(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success));
/** Deny, unpair. */
export const warning = () => run(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning));
export const error = () => run(() => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error));
