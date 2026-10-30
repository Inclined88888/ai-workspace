import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useFocusEffect } from 'expo-router';
import { Client } from './api';
import { useSession } from './session';

type State<T> = { data: T | null; error: string; loading: boolean; refreshing: boolean };

/**
 * Load something from the paired computer while the screen is in view, re-polling every `pollMs` if given.
 * Errors keep the last good data on screen and show a quiet notice instead.
 */
export function useRemote<T>(load: (c: Client) => Promise<T>, pollMs = 0, key = '') {
  const { client, status } = useSession();
  const [st, setSt] = useState<State<T>>({ data: null, error: '', loading: true, refreshing: false });
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(
    async (mode: 'quiet' | 'pull' = 'quiet') => {
      if (!client) return;
      if (mode === 'pull') setSt((s) => ({ ...s, refreshing: true }));
      try {
        const data = await loadRef.current(client);
        setSt({ data, error: '', loading: false, refreshing: false });
      } catch (e) {
        setSt((s) => ({ ...s, error: e instanceof Error ? e.message : String(e), loading: false, refreshing: false }));
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, key],
  );

  useFocusEffect(
    useCallback(() => {
      if (status !== 'online') return;
      run();
      if (!pollMs) return;
      const t = setInterval(() => run(), pollMs);
      return () => clearInterval(t);
    }, [run, pollMs, status]),
  );

  return { ...st, refresh: () => run('pull'), reload: () => run(), set: (data: T) => setSt((s) => ({ ...s, data })) };
}

/* Pending approvals count, shared by the tab bar and the screens that learn it first. */
let pendingCount = 0;
const listeners = new Set<() => void>();
export function setApprovalsCount(n: number) {
  if (n === pendingCount) return;
  pendingCount = n;
  listeners.forEach((l) => l());
}
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function useApprovalsCount(pollMs = 15000): number {
  const { client, status } = useSession();
  useEffect(() => {
    if (!client || status !== 'online') return;
    let live = true;
    const tick = () =>
      client
        .approvals()
        .then((a) => live && setApprovalsCount(a.length))
        .catch(() => undefined);
    tick();
    const t = setInterval(tick, pollMs);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [client, status, pollMs]);
  return useSyncExternalStore(subscribe, () => pendingCount, () => pendingCount);
}
