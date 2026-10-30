import { ApiError, claim, Client, firstReachable } from '../api';

type Call = { url: string; init?: RequestInit };

function fakeFetch(routes: Record<string, { status?: number; body?: unknown; fail?: boolean }>) {
  const calls: Call[] = [];
  const f = jest.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = routes[url];
    if (!r || r.fail) throw new TypeError('Network request failed');
    const status = r.status ?? 200;
    return { ok: status < 400, status, json: async () => r.body } as Response;
  });
  return { f: f as unknown as typeof fetch, calls };
}

describe('api', () => {
  it('picks the first address that answers as AI Workspace', async () => {
    const { f } = fakeFetch({
      'http://a/api/mobile/ping': { fail: true },
      'http://b/api/mobile/ping': { body: { ok: true, app: 'something-else' } },
      'http://c/api/mobile/ping': { body: { ok: true, app: 'ai-workspace' } },
    });
    expect(await firstReachable(['http://a', 'http://b', 'http://c'], f)).toBe('http://c');
    expect(await firstReachable(['http://a'], f)).toBeNull();
  });

  it('claims a code and surfaces the server message', async () => {
    const { f, calls } = fakeFetch({
      'http://c/api/mobile/pair/claim': { body: { device_token: 'wbd_x', device_id: 3, workspace_name: 'Desk' } },
    });
    const r = await claim('http://c', 'ABCDEFGH', 'Phone', 'ios', f);
    expect(r.device_token).toBe('wbd_x');
    expect((calls[0].init?.headers as Record<string, string>)['X-Workbench']).toBe('1');
    const bad = fakeFetch({ 'http://c/api/mobile/pair/claim': { status: 403, body: { detail: 'That code is wrong or has expired' } } });
    await expect(claim('http://c', 'ABCDEFGH', 'Phone', 'ios', bad.f)).rejects.toThrow('That code is wrong or has expired');
  });

  it('sends the device token on reads and the CSRF header on writes', async () => {
    const { f, calls } = fakeFetch({
      'http://c/api/mobile/approvals': { body: [] },
      'http://c/api/mobile/approvals/5': { body: { id: 5, status: 'approved' } },
    });
    const c = new Client('http://c', 'wbd_t', f);
    await c.approvals();
    await c.decide(5, true);
    const [read, write] = calls.map((x) => x.init?.headers as Record<string, string>);
    expect(read.Authorization).toBe('Bearer wbd_t');
    expect(read['X-Workbench']).toBeUndefined();
    expect(write['X-Workbench']).toBe('1');
    expect(calls[1].init?.body).toBe('{"approve":true}');
  });

  it('turns failures into readable errors', async () => {
    const { f } = fakeFetch({ 'http://c/api/mobile/me': { status: 401, body: { detail: 'This phone is not paired' } } });
    const c = new Client('http://c', 'wbd_t', f);
    await expect(c.me()).rejects.toEqual(expect.objectContaining({ message: 'This phone is not paired', status: 401 }));
    const down = new Client('http://down', 'wbd_t', f);
    await expect(down.home()).rejects.toBeInstanceOf(ApiError);
  });

  it('calls the global fetch unbound by default (browsers reject fetch called as a method)', async () => {
    const real = globalThis.fetch;
    let self: unknown = 'unset';
    globalThis.fetch = function (this: unknown) {
      self = this;
      return Promise.resolve(new Response('{"name":"Mac","device_id":1}', { status: 200 }));
    } as typeof fetch;
    try {
      await new Client('http://mac', 't').me();
      expect(self === undefined || self === globalThis).toBe(true);
    } finally {
      globalThis.fetch = real;
    }
  });
});
