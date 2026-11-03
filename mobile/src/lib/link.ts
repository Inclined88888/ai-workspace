/**
 * The encrypted phone link (feat/grok-style). Matches workbench/backend/app/teammates/phonelink.py:
 * X25519 key agreement, HKDF-SHA256 (salt = mailbox, info = "ai-workspace link v1"), ChaCha20-Poly1305 with AAD
 * "<mailbox>|p2c" for phone→computer and "<mailbox>|c2p" back. The device token rides inside the ciphertext, so a relay
 * only ever sees {v, mb, n, c}. Wi-Fi first; the relay when the computer isn't on the same network.
 */
import { chacha20poly1305 } from '@noble/ciphers/chacha';
import { x25519 } from '@noble/curves/ed25519';
import { hkdf } from '@noble/hashes/hkdf';
import { sha256 } from '@noble/hashes/sha256';
import { randomBytes } from '@noble/hashes/utils';

export type Envelope = { v: 1; mb: string; n: string; c: string };
export type Computer = {
  id: string; name: string; token: string; urls: string[]; active: string;
  phonePriv: string; computerPub: string; mailbox: string; relayUrl: string; relayToken: string; seq: number;
};
export type SealedRequest = { method: string; path: string; body?: unknown; idempotency_key?: string };
export type SealedResponse = { seq: number; status: number; body: any };

const enc = new TextEncoder();
const dec = new TextDecoder();
const INFO = enc.encode('ai-workspace link v1');

export function b64e(b: Uint8Array): string {
  let s = '';
  for (const x of b) s += String.fromCharCode(x);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64d(s: string): Uint8Array {
  const t = s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4);
  const bin = atob(t);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

export function newKeyPair(rand: (n: number) => Uint8Array = randomBytes) {
  const priv = rand(32);
  return { priv: b64e(priv), pub: b64e(x25519.getPublicKey(priv)) };
}

export function derive(phonePriv: string, computerPub: string, mailbox: string): Uint8Array {
  const shared = x25519.getSharedSecret(b64d(phonePriv), b64d(computerPub));
  return hkdf(sha256, shared, enc.encode(mailbox), INFO, 32);
}

export function seal(key: Uint8Array, mailbox: string, dir: 'p2c' | 'c2p', obj: unknown, rand: (n: number) => Uint8Array = randomBytes): Envelope {
  const nonce = rand(12);
  const ct = chacha20poly1305(key, nonce, enc.encode(`${mailbox}|${dir}`)).encrypt(enc.encode(JSON.stringify(obj)));
  return { v: 1, mb: mailbox, n: b64e(nonce), c: b64e(ct) };
}

export function open(key: Uint8Array, mailbox: string, dir: 'p2c' | 'c2p', env: Envelope): any {
  if (env.v !== 1 || env.mb !== mailbox) throw new Error('Unknown envelope');
  const pt = chacha20poly1305(key, b64d(env.n), enc.encode(`${mailbox}|${dir}`)).decrypt(b64d(env.c));
  return JSON.parse(dec.decode(pt));
}

/** Wrap one /api/mobile/* call for the link. Bumps the computer's sequence number (the caller saves it). */
export function wrap(c: Computer, req: SealedRequest, now = Date.now()): { env: Envelope; seq: number } {
  if (!req.path.startsWith('/api/mobile/')) throw new Error('Only phone routes go over the link');
  const seq = c.seq + 1;
  const key = derive(c.phonePriv, c.computerPub, c.mailbox);
  return { env: seal(key, c.mailbox, 'p2c', { ...req, seq, ts: now / 1000, token: c.token }), seq };
}

export function unwrap(c: Computer, env: Envelope): SealedResponse {
  return open(derive(c.phonePriv, c.computerPub, c.mailbox), c.mailbox, 'c2p', env);
}

type FetchLike = (url: string, init?: any) => Promise<{ ok: boolean; status: number; json(): Promise<any> }>;

/** Send one sealed request: straight to the computer on Wi-Fi when it answers, otherwise through the relay. */
export async function send(c: Computer, req: SealedRequest, f: FetchLike, save: (c: Computer) => void | Promise<void>): Promise<SealedResponse> {
  const { env, seq } = wrap(c, req);
  await save({ ...c, seq });
  for (const url of [c.active, ...c.urls.filter((u) => u !== c.active)].filter(Boolean)) {
    try {
      const r = await f(`${url}/api/mobile/link/sealed`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' }, body: JSON.stringify(env) });
      if (r.ok) return unwrap(c, await r.json());
      if (r.status === 409 || r.status === 400) throw new Error(r.status === 409 ? 'Refused as a replay' : 'The computer could not open it. Pair again.');
    } catch (e) {
      if (e instanceof Error && /replay|Pair again/.test(e.message)) throw e;
    }
  }
  if (!c.relayUrl) throw new OfflineError();
  const h = { 'Content-Type': 'application/json', Authorization: `Bearer ${c.relayToken}` };
  const post = await f(`${c.relayUrl}/relay/v1/${c.mailbox}/to-computer`, { method: 'POST', headers: h, body: JSON.stringify(env) }).catch(() => null);
  if (!post || !post.ok) throw new OfflineError();
  for (let i = 0; i < 3; i++) {
    const r = await f(`${c.relayUrl}/relay/v1/${c.mailbox}/to-phone?wait=20`, { headers: h }).catch(() => null);
    if (!r || !r.ok) continue;
    for (const m of ((await r.json()).messages ?? []) as Envelope[]) {
      try {
        const res = unwrap(c, m);
        if (res.seq === seq) return res;
      } catch { /* not ours or tampered: skip */ }
    }
  }
  throw new OfflineError();
}

export class OfflineError extends Error {
  constructor() { super("Can't reach your computer right now. It's queued and will go when it's back."); }
}

/* Offline queue ---------------------------------------------------------------------------------------------------
   Writes made while the computer is unreachable (approve, send a draft, hand off) wait here and go in order when it's
   back. Each carries an idempotency key, so a retry after a lost reply never runs twice. */
export type Queued = { id: string; computerId: string; req: SealedRequest; at: number; tries: number; label: string };

export function enqueue(q: Queued[], computerId: string, req: SealedRequest, label: string, now = Date.now()): Queued[] {
  const id = req.idempotency_key ?? `q_${now.toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  if (q.some((x) => x.id === id)) return q;
  return [...q, { id, computerId, req: { ...req, idempotency_key: id }, at: now, tries: 0, label }];
}

export async function flush(q: Queued[], run: (item: Queued) => Promise<SealedResponse>, maxTries = 8):
  Promise<{ left: Queued[]; done: { item: Queued; res: SealedResponse }[] }> {
  const left: Queued[] = [];
  const done: { item: Queued; res: SealedResponse }[] = [];
  let offline = false;
  for (const item of q) {
    if (offline) { left.push(item); continue; }
    try {
      done.push({ item, res: await run(item) });
    } catch (e) {
      if (e instanceof OfflineError) offline = true;
      if (item.tries + 1 < maxTries) left.push({ ...item, tries: item.tries + 1 });
    }
  }
  return { left, done };
}

/* Several computers ------------------------------------------------------------------------------------------------ */
export function upsertComputer(list: Computer[], c: Computer): Computer[] {
  return [...list.filter((x) => x.id !== c.id), c].sort((a, b) => a.name.localeCompare(b.name));
}

/* Key exchange ------------------------------------------------------------------------------------------------------
   Right after pairing (and from "Link securely" for phones paired before this existed), the phone makes a key pair,
   sends only the public half with its device token, and keeps the private half in the secure store. */
export async function linkKeys(base: string, token: string, name: string, f: FetchLike, rand: (n: number) => Uint8Array = randomBytes): Promise<Computer> {
  const kp = newKeyPair(rand);
  const r = await f(`${base}/api/mobile/link/keys`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Workbench': '1', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ phone_pub: kp.pub }),
  });
  if (!r.ok) throw new Error(`Couldn't set up the encrypted link (HTTP ${r.status})`);
  const j = await r.json();
  const urls = Array.from(new Set([base, ...((j.lan as string[]) ?? [])]));
  return { id: j.computer_id, name: j.computer_name || name || 'My computer', token, urls, active: base, phonePriv: kp.priv,
    computerPub: j.computer_pub, mailbox: j.mailbox, relayUrl: j.relay_url || '', relayToken: j.relay_token, seq: 0 };
}
