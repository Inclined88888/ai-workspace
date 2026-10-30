import { decisionFor, handleLockAction } from '../lockActions';
import { b64d, b64e, Computer, derive, enqueue, flush, newKeyPair, OfflineError, open, seal, send, unwrap, upsertComputer, wrap } from '../link';

// Produced by workbench/backend/app/teammates/phonelink.py (Python cryptography) with fixed keys.
const V = {
  computerPub: 'B6N8vBQgk8i3VdwbEOhstCY3StFqqFPtC9_AsrhtHHw',
  phonePriv: 'ISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0-P0A',
  phonePub: 'WGmv9FBUlzLLqu1eXfmzCm2jHLDldCutWtShp2jxpns',
  mailbox: 'mb_test',
  key: 'D0K4Ci32kzvHbAaMKLVh5xIcw9Bcl96qAT_w2yOWFlQ',
  c2p: { v: 1 as const, mb: 'mb_test', n: 'K4CPAnK6UUI4CC9A', c: '7vlFVQsLtrGr3_HuF4O88MbFi3Yz5xZGO53jW0npWDG_0tLFyU26sq4JHaKVlJ4_FR15eltZIJkeNcUc61IEQHwJKO_K' },
};
const comp = (over: Partial<Computer> = {}): Computer => ({ id: 'c_1', name: 'Studio', token: 'device-token-xyz', urls: ['http://lan'], active: 'http://lan',
  phonePriv: V.phonePriv, computerPub: V.computerPub, mailbox: V.mailbox, relayUrl: 'https://relay', relayToken: 'rt', seq: 0, ...over });

describe('phone link crypto (interop with the computer)', () => {
  it('derives the same key as the computer and opens its envelope', () => {
    expect(b64e(derive(V.phonePriv, V.computerPub, V.mailbox))).toBe(V.key);
    expect(unwrap(comp(), V.c2p)).toEqual({ seq: 3, status: 200, body: { hello: 'phone' } });
  });
  it('refuses tampered envelopes and the wrong direction', () => {
    const key = b64d(V.key);
    expect(() => open(key, V.mailbox, 'c2p', { ...V.c2p, c: V.c2p.c.slice(0, -4) + 'AAAA' })).toThrow();
    expect(() => open(key, V.mailbox, 'p2c', V.c2p)).toThrow();
    expect(() => open(key, 'mb_other', 'c2p', V.c2p)).toThrow();
  });
  it('round-trips and keeps the token inside the ciphertext', () => {
    const { env, seq } = wrap(comp({ seq: 4 }), { method: 'GET', path: '/api/mobile/teammates' });
    expect(seq).toBe(5);
    expect(JSON.stringify(env)).not.toContain('device-token-xyz');
    expect(open(b64d(V.key), V.mailbox, 'p2c', env)).toMatchObject({ seq: 5, token: 'device-token-xyz', path: '/api/mobile/teammates' });
    expect(() => wrap(comp(), { method: 'GET', path: '/api/settings' })).toThrow();
  });
  it('makes fresh key pairs', () => {
    const a = newKeyPair();
    expect(b64d(a.pub)).toHaveLength(32);
    expect(newKeyPair().priv).not.toBe(a.priv);
  });
});

describe('sending: Wi-Fi first, then the relay', () => {
  const key = b64d(V.key);
  const answer = (seq: number) => seal(key, V.mailbox, 'c2p', { seq, status: 200, body: { ok: true } });
  it('uses the computer directly when it answers', async () => {
    const calls: string[] = [];
    const f = async (url: string, init?: any) => { calls.push(url); const req = open(key, V.mailbox, 'p2c', JSON.parse(init.body)); return { ok: true, status: 200, json: async () => answer(req.seq) }; };
    const saved: Computer[] = [];
    const r = await send(comp(), { method: 'GET', path: '/api/mobile/teammates' }, f, (c) => { saved.push(c); });
    expect(r.body).toEqual({ ok: true });
    expect(calls).toEqual(['http://lan/api/mobile/link/sealed']);
    expect(saved[0].seq).toBe(1);
  });
  it('falls back to the relay and matches the answer by sequence', async () => {
    const calls: string[] = [];
    const f = async (url: string) => {
      calls.push(url);
      if (url.startsWith('http://lan')) throw new Error('no route');
      if (url.endsWith('/to-computer')) return { ok: true, status: 200, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ messages: [answer(99), answer(1)] }) };
    };
    const r = await send(comp(), { method: 'POST', path: '/api/mobile/outbox/1/send' }, f, () => {});
    expect(r.seq).toBe(1);
    expect(calls[1]).toBe('https://relay/relay/v1/mb_test/to-computer');
  });
  it('says offline when nothing answers', async () => {
    const f = async () => { throw new Error('down'); };
    await expect(send(comp({ relayUrl: '' }), { method: 'GET', path: '/api/mobile/me' }, f, () => {})).rejects.toBeInstanceOf(OfflineError);
  });
});

describe('offline queue and several computers', () => {
  it('queues once per key and flushes in order, stopping when offline', async () => {
    let q = enqueue([], 'c_1', { method: 'POST', path: '/api/mobile/outbox/1/send', idempotency_key: 'k1' }, 'Send quote');
    q = enqueue(q, 'c_1', { method: 'POST', path: '/api/mobile/outbox/1/send', idempotency_key: 'k1' }, 'Send quote');
    q = enqueue(q, 'c_1', { method: 'POST', path: '/api/mobile/link/handoff', body: { draft: 'x' } }, 'Hand off');
    expect(q).toHaveLength(2);
    let off = await flush(q, async () => { throw new OfflineError(); });
    expect(off.left.map((x) => x.tries)).toEqual([1, 0]);
    off = await flush(off.left, async (i) => ({ seq: 1, status: 200, body: i.label }));
    expect(off.left).toEqual([]);
    expect(off.done.map((d) => d.res.body)).toEqual(['Send quote', 'Hand off']);
  });
  it('keeps one entry per computer', () => {
    const l = upsertComputer(upsertComputer([], comp({ id: 'b', name: 'Work PC' })), comp({ id: 'a', name: 'Home Mac' }));
    expect(upsertComputer(l, comp({ id: 'b', name: 'Work laptop' })).map((c) => c.name)).toEqual(['Home Mac', 'Work laptop']);
  });
});

describe('lock-screen approval actions', () => {
  it('maps actions to decisions and ignores junk', () => {
    expect(decisionFor('approve', { kind: 'approval', approval_id: 7 })).toEqual({ kind: 'decide', id: 7, approve: true });
    expect(decisionFor('decline', { kind: 'approval', id: 7 })).toEqual({ kind: 'decide', id: 7, approve: false });
    expect(decisionFor('approve', { kind: 'run', id: 7 })).toBeNull();
    expect(decisionFor('approve', { kind: 'approval', id: 'x' })).toBeNull();
    expect(decisionFor('com.apple.UNNotificationDefaultActionIdentifier', { kind: 'approval', id: 3 })).toEqual({ kind: 'open', path: '/approvals' });
  });
});

describe('lock-screen handler', () => {
  const deps = (ok: boolean, fail = false) => {
    const log: string[] = [];
    return { log, d: { confirm: async () => ({ ok }), decide: async (id: number, a: boolean) => { if (fail) throw new Error('x'); log.push(`decide ${id} ${a}`); },
      open: (p: string) => log.push(`open ${p}`), notify: (t: string) => { log.push(`notify ${t}`); } } };
  };
  it('approves only after Face ID', async () => {
    const a = deps(true);
    expect(await handleLockAction('approve', { kind: 'approval', id: 4, tool: 'send_email' }, a.d)).toBe('approved');
    expect(a.log).toEqual(['decide 4 true', 'notify Approved']);
    const b = deps(false);
    expect(await handleLockAction('approve', { kind: 'approval', id: 4 }, b.d)).toBe('not-confirmed');
    expect(b.log).toEqual(['open /approvals']);
  });
  it('declines with the check too, and falls back to the app on errors', async () => {
    const a = deps(true);
    expect(await handleLockAction('decline', { kind: 'approval', id: 5 }, a.d)).toBe('declined');
    const b = deps(true, true);
    expect(await handleLockAction('approve', { kind: 'approval', id: 5 }, b.d)).toBe('failed');
    expect(b.log).toEqual(['open /approvals']);
  });
});
