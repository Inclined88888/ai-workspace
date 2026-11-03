/**
 * App-wide state: the pairing (token + addresses), the live client, reachability, and display prefs.
 * The token never leaves SecureStore except to be sent as a Bearer header to the paired computer.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState, Platform, useColorScheme } from 'react-native';
import { claim, Client, firstReachable } from './api';
import { clearPairing, DEFAULT_PREFS, loadPairing, loadPrefs, Pairing, Prefs, savePairing, savePrefs } from './storage';
import { DARK_THEMES, Palette, palettes, resolveTheme, ThemeId } from './theme';
import { setHaptics } from './haptics';

type Status = 'loading' | 'unpaired' | 'connecting' | 'online' | 'offline';

type Session = {
  status: Status;
  pairing: Pairing | null;
  client: Client | null;
  prefs: Prefs;
  colors: Palette;
  dark: boolean;
  theme: ThemeId;
  setPrefs: (p: Partial<Prefs>) => void;
  pair: (code: string, urls: string[], deviceName: string) => Promise<void>;
  forget: () => Promise<void>;
  reconnect: () => Promise<void>;
};

const Ctx = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>('loading');
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [prefs, setPrefsState] = useState<Prefs>(DEFAULT_PREFS);
  const system = useColorScheme();
  const busy = useRef(false);

  const connect = useCallback(async (p: Pairing) => {
    if (busy.current) return;
    busy.current = true;
    setStatus('connecting');
    const order = [p.active, ...p.urls.filter((u) => u !== p.active)].filter(Boolean);
    const hit = await firstReachable(order);
    busy.current = false;
    if (!hit) {
      setStatus('offline');
      return;
    }
    if (hit !== p.active) {
      const next = { ...p, active: hit };
      setPairing(next);
      await savePairing(next);
    }
    setStatus('online');
  }, []);

  useEffect(() => {
    (async () => {
      const [p, pr] = await Promise.all([loadPairing(), loadPrefs()]);
      setPrefsState(pr);
      setHaptics(pr.haptics);
      if (!p) {
        setStatus('unpaired');
        return;
      }
      setPairing(p);
      await connect(p);
    })();
  }, [connect]);

  // Coming back to the app (or the Wi-Fi coming back) re-checks which address answers.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => {
      if (s === 'active' && pairing) connect(pairing);
    });
    return () => sub.remove();
  }, [pairing, connect]);

  const pair = useCallback(async (code: string, urls: string[], deviceName: string) => {
    const base = await firstReachable(urls, fetch, 4000);
    if (!base) throw new Error('Could not reach your computer at that address. Check it is on the same Wi-Fi and that "Allow phones on Wi-Fi" is on.');
    const platform = Platform.OS === 'ios' || Platform.OS === 'android' || Platform.OS === 'web' ? Platform.OS : '';
    const r = await claim(base, code, deviceName, platform);
    const p: Pairing = { token: r.device_token, urls, active: base, name: r.workspace_name, deviceId: r.device_id };
    await savePairing(p);
    setPairing(p);
    setStatus('online');
  }, []);

  const forget = useCallback(async () => {
    await clearPairing();
    setPairing(null);
    setStatus('unpaired');
  }, []);

  const setPrefs = useCallback((patch: Partial<Prefs>) => {
    setPrefsState((cur) => {
      const next = { ...cur, ...patch };
      setHaptics(next.haptics);
      savePrefs(next).catch(() => undefined);
      return next;
    });
  }, []);

  const client = useMemo(() => (pairing ? new Client(pairing.active, pairing.token) : null), [pairing]);

  // "Same as computer": read the computer's look whenever we (re)connect, and remember it for offline starts.
  useEffect(() => {
    if (status !== 'online' || !client) return;
    client.appearance().then((a) => {
      const t = a.theme === 'auto' ? 'auto' : a.resolved || a.theme;
      setPrefsState((cur) => {
        if (cur.desktopTheme === t) return cur;
        const next = { ...cur, desktopTheme: t };
        savePrefs(next).catch(() => undefined);
        return next;
      });
    }).catch(() => undefined); // an older computer without /appearance: keep the last known look
  }, [status, client]);

  const theme = resolveTheme(prefs.theme, system, prefs.desktopTheme);
  const dark = DARK_THEMES.includes(theme);
  const value: Session = {
    status,
    pairing,
    client,
    prefs,
    colors: palettes[theme],
    dark,
    theme,
    setPrefs,
    pair,
    forget,
    reconnect: async () => {
      if (pairing) await connect(pairing);
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error('useSession outside SessionProvider');
  return s;
}

export const useColors = () => useSession().colors;
