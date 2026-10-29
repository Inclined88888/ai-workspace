import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { createLauncher } from '../server.mjs';
const fixture = fileURLToPath(new URL('./fake-cli.mjs', import.meta.url));
const token = 'test-bearer-key-with-at-least-24-characters';
async function start(t, extra = {}) {
  const launcher = createLauncher({ command: process.execPath, args: [fixture, '--print'],
    authArgs: [fixture, 'auth', 'status'], authMatch: { loggedIn: true, authMethod: 'claude.ai' },
    token, cwd: process.cwd(), env: { CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1' },
    timeoutMs: 2000, killGraceMs: 50, ...extra });
  launcher.server.listen(0, '127.0.0.1');
  await once(launcher.server, 'listening');
  t.after(() => launcher.stop());
  const url = `http://127.0.0.1:${launcher.server.address().port}`;
  return { ...launcher, url, call: (path, task, headers = {}) => fetch(url + path, {
    method: task === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...headers },
    body: task === undefined ? undefined : JSON.stringify(task),
  }) };
}
test('auth required; status omits identity and credentials', async t => {
  const s = await start(t);
  assert.equal((await fetch(s.url + '/auth/status')).status, 401);
  assert.deepEqual(await (await s.call('/auth/status')).json(), { authenticated: true });
  assert.equal((await s.call('/run', { task: 'hello' }, { Authorization: 'bad' })).status, 401);
});
test('stdin is literal, flags fixed, telemetry off; repeated runs exit', async t => {
  const s = await start(t);
  for (const task of ['--dangerously-skip-permissions $(touch /tmp/launcher-injection)', 'second']) {
    const r = await s.call('/run', { task });
    assert.equal(r.status, 200);
    const result = JSON.parse((await r.json()).output);
    assert.equal(result.task, task);
    assert.deepEqual(result.args, ['--print']);
    assert.equal(result.telemetry, '1');
  }
});
for (const AUTH of ['out', 'api', 'fail']) test(`refuses ${AUTH} authentication`, async t => {
  const s = await start(t, { env: { AUTH } });
  assert.deepEqual(await (await s.call('/auth/status')).json(), { authenticated: false });
  assert.equal((await s.call('/run', { task: 'hello' })).status, 409);
});
test('rejects malformed, oversized and command-changing requests', async t => {
  const s = await start(t);
  for (const body of [{ task: '' }, { task: 1 }, { task: 'a', args: ['evil'] }, {}]) {
    assert.equal((await s.call('/run', body)).status, 400);
  }
  assert.equal((await s.call('/run', { task: 'x'.repeat(70000) })).status, 413);
  assert.equal((await s.call('/run', { task: 'hello' }, { 'Content-Type': 'text/plain' })).status, 415);
});
test('failure diagnostics never reach the response', async t => {
  const s = await start(t);
  const r = await s.call('/run', { task: 'fail' });
  assert.equal(r.status, 502);
  assert.doesNotMatch(await r.text(), /secret-token/);
});
test('output limit and timeout terminate CLI; slot reusable', async t => {
  const s = await start(t, { timeoutMs: 400, maxOutputBytes: 1024 });
  for (const task of ['overflow', 'hang', 'tree']) {
    assert.equal((await s.call('/run', { task })).status, 502);
    assert.equal((await s.call('/run', { task: 'after' })).status, 200);
  }
});
test('busy admission and client disconnect terminate a run', async t => {
  const s = await start(t);
  const controller = new AbortController();
  const running = fetch(s.url + '/run', { method: 'POST', signal: controller.signal,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ task: 'hang' }) }).catch(() => null);
  // Let the first HTTP request reach the server before checking admission.
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal((await s.call('/run', { task: 'second' })).status, 503);
  controller.abort();
  await running;
  for (let i = 0; i < 100; i++) {
    const response = await s.call('/auth/status');
    if (response.status === 200) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail('slot was not released after disconnect');
});
test('shutdown terminates an active process', async t => {
  const s = await start(t);
  const running = s.call('/run', { task: 'hang' });
  await new Promise(resolve => setTimeout(resolve, 150));
  await s.stop();
  assert.equal((await running).status, 502);
});
test('missing CLI fails safely and releases admission', async t => {
  const s = await start(t, { command: '/missing-official-cli' });
  assert.deepEqual(await (await s.call('/auth/status')).json(), { authenticated: false });
  assert.equal((await s.call('/run', { task: 'hello' })).status, 409);
});
