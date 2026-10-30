/**
 * Where the pairing lives. The device token is a secret: iOS Keychain / Android Keystore through expo-secure-store.
 * The web build (used only for previews and screenshots) has no secure store, so it keeps it for the tab session.
 */
import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import type { MotionPref } from './motion';

export type Pairing = { token: string; urls: string[]; active: string; name: string; deviceId: number };

const KEY = 'aiworkspace.pairing.v1';
const PREFS = 'aiworkspace.prefs.v1';

/** theme: 'computer' follows the paired computer's choice; 'system' follows the phone; or one of the six themes.
 * 'light' and 'dark' are the older choices, read as Paper and Dark. */
export type ThemePref = 'computer' | 'system' | 'sunshine' | 'paper' | 'cherry' | 'neon' | 'night' | 'contrast' | 'light' | 'dark';
export type Prefs = { theme: ThemePref; model: string; haptics: boolean; desktopTheme?: string; motion?: MotionPref };
export const DEFAULT_PREFS: Prefs = { theme: 'computer', model: '', haptics: true, motion: 'full' };

const web = {
  get: (k: string) => (typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(k) : null),
  set: (k: string, v: string) => typeof sessionStorage !== 'undefined' && sessionStorage.setItem(k, v),
  del: (k: string) => typeof sessionStorage !== 'undefined' && sessionStorage.removeItem(k),
};

async function getItem(k: string): Promise<string | null> {
  if (Platform.OS === 'web') return web.get(k);
  return SecureStore.getItemAsync(k);
}

async function setItem(k: string, v: string): Promise<void> {
  if (Platform.OS === 'web') {
    web.set(k, v);
    return;
  }
  await SecureStore.setItemAsync(k, v, { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY });
}

async function delItem(k: string): Promise<void> {
  if (Platform.OS === 'web') {
    web.del(k);
    return;
  }
  await SecureStore.deleteItemAsync(k);
}

export async function loadPairing(): Promise<Pairing | null> {
  try {
    const raw = await getItem(KEY);
    const p = raw ? (JSON.parse(raw) as Pairing) : null;
    return p && p.token && Array.isArray(p.urls) ? p : null;
  } catch {
    return null;
  }
}

export const savePairing = (p: Pairing) => setItem(KEY, JSON.stringify(p));
export const clearPairing = () => delItem(KEY);

export async function loadPrefs(): Promise<Prefs> {
  try {
    const raw = await getItem(PREFS);
    return { ...DEFAULT_PREFS, ...(raw ? JSON.parse(raw) : {}) };
  } catch {
    return DEFAULT_PREFS;
  }
}

export const savePrefs = (p: Prefs) => setItem(PREFS, JSON.stringify(p));
