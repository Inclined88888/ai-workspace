/** @jest-environment node */
/**
 * End to end against a real computer (the Python backend). Skipped unless LINK_E2E_URL and LINK_E2E_CODE are set:
 *   LINK_E2E_URL=http://localhost:8390 LINK_E2E_CODE=<pairing code> npx jest link.e2e
 * Pairs, exchanges keys, then makes sealed calls over /api/mobile/link/sealed and checks a replay is refused.
 */
import { claim } from '../api';
import { linkKeys, send, upsertComputer, wrap } from '../link';

const URL_ = process.env.LINK_E2E_URL;
const CODE = process.env.LINK_E2E_CODE;
const run = URL_ && CODE ? it : it.skip;
// jest-expo replaces fetch with a mock, so this test talks HTTP with Node's http module.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const http = require('node:http');
const realFetch = (url: string, init: any = {}): Promise<{ ok: boolean; status: number; json(): Promise<any> }> =>
  new Promise((resolve, reject) => {
    const req = http.request(url, { method: init.method ?? 'GET', headers: init.headers ?? {} }, (res: any) => {
      let data = '';
      res.on('data', (c: string) => { data += c; });
      res.on('end', () => resolve({ ok: res.statusCode < 400, status: res.statusCode, json: async () => JSON.parse(data || 'null') }));
    });
    req.on('error', reject);
    if (init.body) req.write(init.body);
    req.end();
  });

run('pairs, links keys and talks over the encrypted link', async () => {
  const f = (u: string, i?: any) => realFetch(u, i) as any;
  const r = await claim(URL_!, CODE!.replace(/-/g, ''), 'E2E phone', 'ios', f as unknown as typeof fetch);
  const comp = await linkKeys(URL_!, r.device_token, r.workspace_name, f);
  expect(comp.mailbox).toMatch(/^mb_/);
  const list = upsertComputer([], comp);
  expect(list).toHaveLength(1);
  let saved = comp;
  const res = await send(comp, { method: 'GET', path: '/api/mobile/teammates' }, f, (c) => { saved = c; });
  expect(res.status).toBe(200);
  expect(Array.isArray(res.body.teammates)).toBe(true);
  expect(saved.seq).toBe(1);
  const blocked = await send(saved, { method: 'GET', path: '/api/mobile/me' }, f, (c) => { saved = c; });
  expect(blocked.status).toBe(200);
  // a replayed envelope is refused by the computer
  const { env } = wrap({ ...saved, seq: 0 }, { method: 'GET', path: '/api/mobile/me' });
  const again = await realFetch(`${URL_}/api/mobile/link/sealed`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' }, body: JSON.stringify(env) });
  expect(again.status).toBe(409);
});
